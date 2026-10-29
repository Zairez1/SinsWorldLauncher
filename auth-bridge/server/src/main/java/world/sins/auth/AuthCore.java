package world.sins.auth;

import com.google.gson.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.*;
import java.time.Clock;
import java.util.*;

/** Token state is independent of Bukkit so expiry/replay/revocation can be tested. */
public final class AuthCore {
    public interface Backend {
        String canonicalName(String name) throws Exception;
        boolean checkPassword(String name, String password) throws Exception;
        boolean register(String name, String password) throws Exception;
        void changePassword(String name, String password) throws Exception;
        /** Reject TOTP accounts; return a fingerprint of the current AuthMe password hash. */
        String credentialStamp(String name) throws Exception;
    }
    public static final class Rejected extends RuntimeException {
        public final int status;
        public Rejected(int status, String message) { super(message); this.status = status; }
    }
    record Token(String name, String stamp, String family, long expires) {}
    private final Backend backend;
    private final Clock clock;
    private final Path stateFile;
    private final SecureRandom random = new SecureRandom();
    private final Gson gson = new GsonBuilder().setPrettyPrinting().create();
    private final Map<String, Token> access = new HashMap<>(), refresh = new HashMap<>(), tickets = new HashMap<>();
    private final Map<String, String> recovery = new HashMap<>();
    private final int maxTokens = 10000;
    private final long accessTtl = 15 * 60_000L, refreshTtl = 30L * 86400_000, ticketTtl = 600_000L;
    public AuthCore(Backend backend, Clock clock, Path stateFile) throws IOException {
        this.backend = backend; this.clock = clock; this.stateFile = stateFile;
        if (Files.exists(stateFile)) {
            JsonObject root = JsonParser.parseString(Files.readString(stateFile)).getAsJsonObject();
            if (root.has("recovery")) root.getAsJsonObject("recovery").entrySet().forEach(e -> recovery.put(e.getKey(), e.getValue().getAsString()));
            if (root.has("refresh")) root.getAsJsonObject("refresh").entrySet().forEach(e -> refresh.put(e.getKey(), gson.fromJson(e.getValue(), Token.class)));
        }
        cleanup();
    }
    static void nickname(String name) {
        if (name == null || !name.matches("[A-Za-z0-9_]{3,16}")) throw new Rejected(400, "Invalid nickname");
    }
    static String key(String name) { return name.toLowerCase(Locale.ROOT); }
    public static UUID offlineUuid(String name) {
        return UUID.nameUUIDFromBytes(("OfflinePlayer:" + name).getBytes(StandardCharsets.UTF_8));
    }
    static String digest(String secret) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(secret.getBytes(StandardCharsets.UTF_8))); }
        catch (NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }
    static boolean equal(String a, String b) {
        return a != null && b != null && MessageDigest.isEqual(a.getBytes(StandardCharsets.US_ASCII), b.getBytes(StandardCharsets.US_ASCII));
    }
    private String secret() { byte[] b = new byte[32]; random.nextBytes(b); return Base64.getUrlEncoder().withoutPadding().encodeToString(b); }
    private Map<String, Object> account(String name) { return Map.of("id", offlineUuid(name).toString(), "name", name); }
    private Token validate(Token t) throws Exception {
        if (t == null || t.expires <= clock.millis() || !equal(t.stamp, backend.credentialStamp(t.name)))
            throw new Rejected(401, "Session expired. Sign in again.");
        return t;
    }
    private void cleanup() {
        long now = clock.millis();
        for (var map : List.of(access, refresh, tickets)) map.values().removeIf(t -> t.expires <= now);
    }
    private void capacity() {
        cleanup();
        if (access.size() >= maxTokens || refresh.size() >= maxTokens || tickets.size() >= maxTokens)
            throw new Rejected(503, "Too many sessions; retry later");
    }
    private void save() throws IOException {
        Files.createDirectories(stateFile.toAbsolutePath().getParent());
        var root = new JsonObject(); root.add("recovery", gson.toJsonTree(recovery)); root.add("refresh", gson.toJsonTree(refresh));
        Path tmp = Files.createTempFile(stateFile.toAbsolutePath().getParent(), "auth-state-", ".tmp");
        try {
            try { Files.setPosixFilePermissions(tmp, PosixFilePermissions.fromString("rw-------")); } catch (UnsupportedOperationException ignored) {}
            Files.writeString(tmp, gson.toJson(root));
            try { Files.move(tmp, stateFile, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE); }
            catch (AtomicMoveNotSupportedException e) { Files.move(tmp, stateFile, StandardCopyOption.REPLACE_EXISTING); }
        } finally { Files.deleteIfExists(tmp); }
    }
    private Map<String, Object> issue(String name, String family, boolean allowRecovery) throws Exception {
        capacity();
        String stamp = backend.credentialStamp(name);
        String a = secret(), r = secret(), code = null;
        if (allowRecovery && !recovery.containsKey(key(name))) code = secret();
        Token rt = new Token(name, stamp, family, clock.millis() + refreshTtl);
        refresh.put(digest(r), rt);
        if (code != null) recovery.put(key(name), digest(code));
        try { save(); }
        catch (IOException e) { refresh.remove(digest(r)); if (code != null) recovery.remove(key(name)); throw e; }
        access.put(digest(a), new Token(name, stamp, family, clock.millis() + accessTtl));
        var out = new HashMap<String, Object>(); out.put("account", account(name)); out.put("access_token", a); out.put("refresh_token", r);
        if (code != null) out.put("recovery_code", code);
        return out;
    }
    public synchronized Map<String, Object> login(String name, String password) throws Exception {
        nickname(name);
        if (password == null || password.length() > 128 || !backend.checkPassword(name, password))
            throw new Rejected(401, "Invalid nickname or password");
        return issue(backend.canonicalName(name), secret(), true);
    }
    public synchronized Map<String, Object> register(String name, String password, int min, int max, Set<String> unsafe) throws Exception {
        nickname(name); password(password, min, max, unsafe);
        capacity();
        if (!backend.register(name, password)) throw new Rejected(409, "Nickname already registered");
        return issue(backend.canonicalName(name), secret(), true);
    }
    static void password(String password, int min, int max, Set<String> unsafe) {
        if (password == null || password.length() < min || password.length() > max || unsafe.contains(password.toLowerCase(Locale.ROOT)))
            throw new Rejected(400, "Password must contain " + min + " to " + max + " characters and not be a common password");
    }
    public synchronized Map<String, Object> refresh(String secret) throws Exception {
        String hashed = digest(secret); Token t = validate(refresh.get(hashed));
        // The one atomic state-file replacement persists rotation in one step.
        refresh.remove(hashed);
        try { return issue(t.name, t.family, false); }
        catch (Exception e) { refresh.put(hashed, t); throw e; }
    }
    public synchronized void logout(String token) throws Exception {
        Token t = validate(access.get(digest(token)));
        for (var map : List.of(access, refresh, tickets)) map.values().removeIf(v -> v.family.equals(t.family));
        save();
    }
    public synchronized Map<String, Object> currentAccount(String token) throws Exception {
        Token t = validate(access.get(digest(token)));
        return account(t.name);
    }
    public synchronized Map<String, Object> gameSession(String token, String server) throws Exception {
        Token t = validate(access.get(digest(token))); capacity();
        String ticket = secret(); long expires = clock.millis() + ticketTtl;
        tickets.put(digest(ticket), new Token(t.name, t.stamp, t.family, expires));
        return Map.of("mode", "authme_bridge", "name", t.name, "uuid", offlineUuid(t.name).toString(),
                      "access_token", "0", "ticket", ticket, "server", server, "expires_at", expires);
    }
    /** Compare identity before consuming: a forged nickname cannot burn another player's ticket. */
    public synchronized boolean redeem(String ticket, String name, UUID id) {
        try {
            nickname(name); if (ticket == null || !ticket.matches("[A-Za-z0-9_-]{43}")) return false;
            Token t = validate(tickets.get(digest(ticket)));
            if (!t.name.equals(name) || !offlineUuid(t.name).equals(id)) return false;
            tickets.remove(digest(ticket)); return true;
        } catch (Exception e) { return false; }
    }
    public synchronized void recover(String name, String code, String password, int min, int max, Set<String> unsafe) throws Exception {
        nickname(name); password(password, min, max, unsafe);
        String stored = recovery.get(key(name));
        if (code == null || !equal(stored, digest(code))) throw new Rejected(401, "Invalid recovery code");
        String canonical = backend.canonicalName(name);
        backend.credentialStamp(canonical); // TOTP accounts must use AuthMe's own recovery flow.
        backend.changePassword(canonical, password);
        recovery.remove(key(name));
        for (var map : List.of(access, refresh, tickets)) map.values().removeIf(t -> key(t.name).equals(key(canonical)));
        save();
    }
}
