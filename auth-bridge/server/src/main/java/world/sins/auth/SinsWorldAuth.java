package world.sins.auth;

import com.google.gson.*;
import com.sun.net.httpserver.*;
import org.bukkit.Bukkit;
import org.bukkit.entity.Player;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.plugin.messaging.PluginMessageListener;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.util.*;
import java.util.concurrent.*;

public final class SinsWorldAuth extends JavaPlugin implements PluginMessageListener {
    private static final String CHANNEL = "sinsworld:auth";
    private final Gson gson = new Gson();
    private final Map<String, Bucket> rates = new HashMap<>();
    private record Bucket(long start, int count) {}
    private HttpServer http;
    private ThreadPoolExecutor pool;
    private AuthCore core;
    private BukkitBackend backend;
    private SkinLookup skinLookup;
    private String serverAddress;
    private volatile boolean configured;

    @Override public void onEnable() {
        saveDefaultConfig();
        try {
            var authMe = getServer().getPluginManager().getPlugin("AuthMe");
            if (authMe == null || !authMe.isEnabled() || !authMe.getDescription().getVersion().startsWith("5.7."))
                throw new IllegalStateException("Installed AuthMe 5.7.x is required");
            if (Bukkit.getOnlineMode()) throw new IllegalStateException("This bridge supports the existing offline-UUID AuthMe setup only; no server-mode changes are made");
            backend = new BukkitBackend(this, authMe);
            core = new AuthCore(backend, Clock.systemUTC(), getDataFolder().toPath().resolve("private-auth-state.json"));
            var skinsPlugin = getServer().getPluginManager().getPlugin("SkinsRestorer");
            if (skinsPlugin != null && skinsPlugin.isEnabled()) {
                try {
                    skinLookup = new SkinsRestorerLookup();
                    getLogger().info("Launcher avatars connected to SkinsRestorer");
                } catch (LinkageError | RuntimeException e) {
                    getLogger().warning("SkinsRestorer avatar integration unavailable; account login remains enabled");
                }
            }
            serverAddress = getConfig().getString("server-address", "sinsworld.minecraft.best").trim();
            if (!serverAddress.matches("[A-Za-z0-9.-]+(?::[0-9]{1,5})?")) throw new IllegalStateException("Invalid server-address");
            String publicUrl = getConfig().getString("public-api-url", "").trim();
            if (!publicUrl.isEmpty()) {
                URI u = URI.create(publicUrl);
                configured = "https".equals(u.getScheme()) && u.getHost() != null && u.getUserInfo() == null
                        && (u.getRawPath() == null || u.getRawPath().isEmpty() || "/".equals(u.getRawPath())) && u.getRawQuery() == null && u.getRawFragment() == null;
                if (!configured) throw new IllegalStateException("public-api-url must be an HTTPS origin");
            }
            pool = new ThreadPoolExecutor(4, 8, 30, TimeUnit.SECONDS, new ArrayBlockingQueue<>(64), r -> {
                Thread t = new Thread(r, "sinsworld-auth"); t.setDaemon(true); return t;
            }, new ThreadPoolExecutor.AbortPolicy());
            int port = getConfig().getInt("http-port", 8765);
            if (port < 1024 || port > 65535) throw new IllegalStateException("Invalid HTTP port");
            var bind = new InetSocketAddress(getConfig().getString("http-bind", "0.0.0.0"), port);
            if (getConfig().getBoolean("tls-enabled", false)) {
                String ip = getConfig().getString("tls-ip", "104.204.218.124");
                URI publicUri = URI.create(publicUrl);
                if (!configured || !ip.equals(publicUri.getHost()) || publicUri.getPort() != port)
                    throw new IllegalStateException("Direct TLS requires public-api-url https://tls-ip:http-port");
                var tls = DirectTls.create(getDataFolder().toPath(), ip);
                var secure = HttpsServer.create(bind, 32);
                secure.setHttpsConfigurator(new HttpsConfigurator(tls) {
                    @Override public void configure(HttpsParameters params) {
                        var settings = getSSLContext().getDefaultSSLParameters();
                        settings.setProtocols(new String[]{"TLSv1.3", "TLSv1.2"});
                        params.setSSLParameters(settings);
                    }
                });
                http = secure;
            } else http = HttpServer.create(bind, 32);
            http.createContext("/", this::handle); http.setExecutor(pool); http.start();
            getServer().getMessenger().registerIncomingPluginChannel(this, CHANNEL, this);
            getLogger().info("Auth bridge listening on allocated port " + port + "; HTTPS API " + (configured ? "configured" : "DISABLED until public-api-url is set"));
        } catch (Exception e) {
            getLogger().severe("Cannot start SinsWorldAuth: " + e.getClass().getSimpleName() + ": " + e.getMessage());
            getServer().getPluginManager().disablePlugin(this);
        }
    }
    @Override public void onDisable() {
        if (http != null) http.stop(0);
        if (pool != null) pool.shutdownNow();
        getServer().getMessenger().unregisterIncomingPluginChannel(this);
    }
    private synchronized void rate(String key, int limit, long window) {
        long now = System.currentTimeMillis();
        rates.entrySet().removeIf(e -> now - e.getValue().start > 3600_000L);
        if (rates.size() > 10000) throw new AuthCore.Rejected(429, "Retry later");
        Bucket b = rates.get(key);
        if (b == null || now - b.start >= window) b = new Bucket(now, 0);
        if (b.count >= limit) throw new AuthCore.Rejected(429, "Too many attempts; retry later");
        rates.put(key, new Bucket(b.start, b.count + 1));
    }
    private static String field(JsonObject body, String key, int max) {
        JsonElement e = body.get(key);
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) throw new AuthCore.Rejected(400, "Missing field: " + key);
        String value = e.getAsString();
        if (value.length() > max) throw new AuthCore.Rejected(400, "Field too long: " + key);
        return value;
    }
    private static String bearer(HttpExchange request) {
        String value = request.getRequestHeaders().getFirst("Authorization");
        if (value == null || !value.matches("Bearer [A-Za-z0-9_-]{43}")) throw new AuthCore.Rejected(401, "Sign in required");
        return value.substring(7);
    }
    private void handle(HttpExchange request) throws IOException {
        try {
            String route = request.getRequestURI().getPath();
            String address = request.getRemoteAddress().getAddress().getHostAddress();
            rate("ip:" + address, Math.max(10, getConfig().getInt("requests-per-minute", 120)), 60_000);
            if (route.equals("/health") && request.getRequestMethod().equals("GET")) {
                reply(request, 200, Map.of("service", "SinsWorldAuth", "protocol", 1, "authme", "5.7", "configured", configured, "skin_avatars", skinLookup != null)); return;
            }
            if (!configured) throw new AuthCore.Rejected(503, "HTTPS endpoint is not configured on the server");
            String minimum = getConfig().getString("minimum-launcher-version", "1.2.5").trim();
            if (!minimum.isEmpty() && !LauncherPolicy.accepts(request.getRequestHeaders().getFirst("User-Agent"), minimum))
                throw new AuthCore.Rejected(426, "Лаунчер устарел. Нужна версия " + minimum + ". Обновите лаунчер для продолжения.");
            if (!request.getRequestMethod().equals("POST")) throw new AuthCore.Rejected(405, "POST required");
            if (request.getRequestHeaders().containsKey("Origin")) throw new AuthCore.Rejected(403, "Browser requests are not supported");
            String type = request.getRequestHeaders().getFirst("Content-Type");
            if (type == null || !type.toLowerCase(Locale.ROOT).startsWith("application/json")) throw new AuthCore.Rejected(415, "JSON required");
            byte[] bytes = request.getRequestBody().readNBytes(8193);
            if (bytes.length > 8192) throw new AuthCore.Rejected(413, "Request too large");
            JsonObject body;
            try { body = JsonParser.parseString(new String(bytes, StandardCharsets.UTF_8)).getAsJsonObject(); }
            catch (RuntimeException e) { throw new AuthCore.Rejected(400, "Invalid JSON"); }
            Object out;
            switch (route) {
                case "/v1/accounts/register" -> {
                    rate("register:" + address, Math.max(1, getConfig().getInt("registration-per-address-per-hour", 5)), 3600_000);
                    out = core.register(field(body, "nickname", 16), field(body, "password", 128), backend.minRegistration, backend.maxPassword, backend.unsafe);
                }
                case "/v1/sessions/login" -> {
                    String name = field(body, "nickname", 16);
                    rate("login:" + name.toLowerCase(Locale.ROOT), Math.max(1, getConfig().getInt("failed-logins-per-account-per-minute", 10)), 60_000);
                    out = core.login(name, field(body, "password", 128));
                }
                case "/v1/sessions/refresh" -> out = core.refresh(field(body, "refresh_token", 128));
                case "/v1/sessions/logout" -> { core.logout(bearer(request)); out = Map.of("ok", true); }
                case "/v1/profile/skin" -> {
                    var identity = core.currentAccount(bearer(request));
                    String name = (String) identity.get("name");
                    rate("skin:" + name.toLowerCase(Locale.ROOT), 4, 60_000);
                    var texture = skinLookup == null ? Optional.<String>empty()
                            : skinLookup.texture(UUID.fromString((String) identity.get("id")), name);
                    out = texture.isPresent() ? Map.of("source", "skinsrestorer", "available", true, "texture_url", texture.get())
                            : Map.of("source", "skinsrestorer", "available", false);
                }
                case "/v1/game/session" -> out = core.gameSession(bearer(request), serverAddress);
                case "/v1/account/recover" -> {
                    String name = field(body, "nickname", 16);
                    rate("recover:" + name.toLowerCase(Locale.ROOT), 5, 3600_000);
                    core.recover(name, field(body, "recovery_code", 128), field(body, "password", 128), backend.minRegistration, backend.maxPassword, backend.unsafe);
                    out = Map.of("ok", true);
                }
                default -> throw new AuthCore.Rejected(404, "Unknown route");
            }
            reply(request, 200, out);
        } catch (AuthCore.Rejected e) { reply(request, e.status, Map.of("error", e.getMessage())); }
        catch (Exception e) {
            // No body, password, recovery code, session/ticket or database row in logs.
            getLogger().warning("Account operation failed (" + e.getClass().getSimpleName() + ")");
            reply(request, 503, Map.of("error", "Account service unavailable; retry later"));
        } finally { request.close(); }
    }
    private void reply(HttpExchange request, int status, Object result) throws IOException {
        byte[] bytes = gson.toJson(result).getBytes(StandardCharsets.UTF_8);
        request.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        request.getResponseHeaders().set("Cache-Control", "no-store");
        request.getResponseHeaders().set("X-Content-Type-Options", "nosniff");
        request.sendResponseHeaders(status, bytes.length);
        try (var out = request.getResponseBody()) { out.write(bytes); }
    }
    @Override public void onPluginMessageReceived(String channel, Player player, byte[] message) {
        if (!CHANNEL.equals(channel) || message.length != 43 || !configured) return;
        String ticket = new String(message, StandardCharsets.US_ASCII);
        String name = player.getName(); UUID id = player.getUniqueId();
        // Never block the Bukkit thread waiting for an HTTP/core/database operation.
        try { pool.execute(() -> {
            if (!core.redeem(ticket, name, id)) return;
            Bukkit.getScheduler().runTaskLater(this, () -> {
                if (!player.isOnline() || Bukkit.getPlayer(id) != player || !player.getName().equals(name) || !player.getUniqueId().equals(id)) return;
                try {
                    if (backend.authenticated(player)) return;
                    backend.forceLogin(player);
                    Bukkit.getScheduler().runTaskLater(this, () -> {
                        try {
                            if (player.isOnline() && Bukkit.getPlayer(id) == player && !backend.authenticated(player))
                                player.kickPlayer("SinsWorld: automatic login could not be completed. Reopen the launcher and sign in again.");
                        } catch (Exception e) { if (player.isOnline() && Bukkit.getPlayer(id) == player) player.kickPlayer("SinsWorld authentication unavailable"); }
                    }, 100L);
                } catch (Exception e) { player.kickPlayer("SinsWorld authentication unavailable"); }
            }, 10L);
        }); } catch (RejectedExecutionException ignored) { /* AuthMe keeps the player unauthenticated. */ }
    }
}
