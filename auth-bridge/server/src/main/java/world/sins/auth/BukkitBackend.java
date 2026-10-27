package world.sins.auth;

import org.bukkit.Bukkit;
import org.bukkit.configuration.file.YamlConfiguration;
import org.bukkit.entity.Player;
import org.bukkit.plugin.Plugin;
import java.lang.reflect.*;
import java.nio.file.Path;
import java.sql.*;
import java.util.*;
import java.util.concurrent.TimeUnit;

/** Calls the installed 5.7 API; never implements or rewrites AuthMe password hashes. */
final class BukkitBackend implements AuthCore.Backend {
    private final SinsWorldAuth owner;
    private final Object api;
    private final Class<?> apiType, infoType;
    private final Path database;
    private final String table;
    private final Properties readOnly = new Properties();
    final int minRegistration, maxPassword;
    final Set<String> unsafe;
    BukkitBackend(SinsWorldAuth owner, Plugin authMe) throws Exception {
        this.owner = owner;
        var loader = authMe.getClass().getClassLoader();
        apiType = Class.forName("fr.xephi.authme.api.v3.AuthMeApi", true, loader);
        infoType = Class.forName("fr.xephi.authme.api.v3.AuthMePlayer", true, loader);
        api = apiType.getMethod("getInstance").invoke(null);
        for (String method : List.of("isRegistered", "getPlayerInfo")) apiType.getMethod(method, String.class);
        apiType.getMethod("checkPassword", String.class, String.class);
        apiType.getMethod("registerPlayer", String.class, String.class);
        apiType.getMethod("changePassword", String.class, String.class);
        apiType.getMethod("forceLogin", Player.class);
        var config = YamlConfiguration.loadConfiguration(new java.io.File(authMe.getDataFolder(), "config.yml"));
        if (!"SQLITE".equalsIgnoreCase(config.getString("DataSource.backend", "")))
            throw new IllegalStateException("This build requires the supplied AuthMe SQLITE configuration");
        maxPassword = config.getInt("settings.security.passwordMaxLength", 30);
        minRegistration = Math.max(config.getInt("settings.security.minPasswordLength", 5), owner.getConfig().getInt("registration-min-password", 12));
        if (minRegistration > maxPassword) throw new IllegalStateException("Invalid password policy");
        unsafe = new HashSet<>();
        for (String word : config.getStringList("settings.security.unsafePasswords")) unsafe.add(word.toLowerCase(Locale.ROOT));
        database = Path.of(owner.getConfig().getString("authme-database", "plugins/AuthMe/authme.db")).toAbsolutePath();
        table = owner.getConfig().getString("authme-table", "authme");
        if (!table.matches("[A-Za-z0-9_]+") || !java.nio.file.Files.isRegularFile(database))
            throw new IllegalStateException("AuthMe database/table unavailable");
        Class.forName("org.sqlite.JDBC");
        readOnly.setProperty("open_mode", "1"); // SQLITE_OPEN_READONLY
        try (Connection c = connection(); var q = c.prepareStatement("SELECT password,totp FROM \"" + table + "\" LIMIT 1")) { q.executeQuery().close(); }
    }
    private Connection connection() throws SQLException {
        Connection c = DriverManager.getConnection("jdbc:sqlite:" + database, readOnly);
        try (var statement = c.createStatement()) {
            statement.execute("PRAGMA query_only=ON"); statement.execute("PRAGMA busy_timeout=3000");
        }
        return c;
    }
    private Object invoke(String method, Class<?>[] types, Object... args) throws Exception {
        try { return apiType.getMethod(method, types).invoke(api, args); }
        catch (InvocationTargetException e) { throw new IllegalStateException("AuthMe API operation failed", e.getCause()); }
    }
    @Override public String canonicalName(String name) throws Exception {
        @SuppressWarnings("unchecked") var info = (Optional<Object>) invoke("getPlayerInfo", new Class<?>[]{String.class}, name);
        if (info.isEmpty()) throw new AuthCore.Rejected(401, "Invalid nickname or password");
        return (String) infoType.getMethod("getName").invoke(info.get());
    }
    @Override public String credentialStamp(String name) throws Exception {
        try (var c = connection(); var q = c.prepareStatement("SELECT password,totp FROM \"" + table + "\" WHERE username=?")) {
            q.setString(1, name.toLowerCase(Locale.ROOT));
            try (var result = q.executeQuery()) {
                if (!result.next()) throw new AuthCore.Rejected(401, "Invalid nickname or password");
                String totp = result.getString(2);
                if (totp != null && !totp.isBlank())
                    throw new AuthCore.Rejected(403, "AuthMe two-factor accounts require the server's normal login flow");
                return AuthCore.digest(result.getString(1));
            }
        }
    }
    @Override public boolean checkPassword(String name, String password) throws Exception {
        credentialStamp(name);
        return (boolean) invoke("checkPassword", new Class<?>[]{String.class, String.class}, name, password);
    }
    @Override public boolean register(String name, String password) throws Exception {
        return (boolean) invoke("registerPlayer", new Class<?>[]{String.class, String.class}, name, password);
    }
    @Override public void changePassword(String name, String password) throws Exception {
        Bukkit.getScheduler().callSyncMethod(owner, () -> { invoke("changePassword", new Class<?>[]{String.class, String.class}, name, password); return true; }).get(5, TimeUnit.SECONDS);
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(8);
        while (System.nanoTime() < deadline) {
            if ((boolean) invoke("checkPassword", new Class<?>[]{String.class, String.class}, name, password)) return;
            Thread.sleep(100);
        }
        throw new IllegalStateException("AuthMe did not confirm the password change");
    }
    void forceLogin(Player player) throws Exception { invoke("forceLogin", new Class<?>[]{Player.class}, player); }
    boolean authenticated(Player player) throws Exception { return (boolean) invoke("isAuthenticated", new Class<?>[]{Player.class}, player); }
}
