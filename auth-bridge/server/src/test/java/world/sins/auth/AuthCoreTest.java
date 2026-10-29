package world.sins.auth;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;
import static org.junit.jupiter.api.Assertions.*;

class AuthCoreTest {
    @TempDir Path dir;
    final String password = "LongPassword1234";
    static class FakeBackend implements AuthCore.Backend {
        final Map<String,String> pass = new HashMap<>(), names = new HashMap<>();
        boolean totp;
        public String canonicalName(String n) { return names.get(n.toLowerCase(Locale.ROOT)); }
        public boolean checkPassword(String n,String p) { return Objects.equals(pass.get(n.toLowerCase(Locale.ROOT)),p); }
        public boolean register(String n,String p) { String k=n.toLowerCase(Locale.ROOT); if(pass.containsKey(k))return false; pass.put(k,p);names.put(k,n);return true; }
        public void changePassword(String n,String p) { pass.put(n.toLowerCase(Locale.ROOT),p); }
        public String credentialStamp(String n) { if(totp)throw new AuthCore.Rejected(403,"TOTP"); return AuthCore.digest(pass.get(n.toLowerCase(Locale.ROOT))); }
    }
    static class MutableClock extends Clock {
        long millis=1000000;
        public ZoneId getZone(){return ZoneOffset.UTC;}
        public Clock withZone(ZoneId zone){return this;}
        public Instant instant(){return Instant.ofEpochMilli(millis);}
        public long millis(){return millis;}
    }
    FakeBackend backend;
    MutableClock clock;
    AuthCore core;
    @BeforeEach void setup() throws Exception { backend=new FakeBackend(); clock=new MutableClock(); core=new AuthCore(backend,clock,dir.resolve("state.json")); }
    Map<String,Object> register() throws Exception { return core.register("Zaire",password,12,30,Set.of()); }
    String field(Map<String,Object> o,String k){return (String)o.get(k);}
    @Test void wrongPasswordDoesNotIssueSession() throws Exception {
        register();assertThrows(AuthCore.Rejected.class,()->core.login("Zaire","wrong"));
    }
    @Test void preservesCanonicalNameAndOfflineUuid() throws Exception {
        register();var login=core.login("zaire",password);
        @SuppressWarnings("unchecked")var a=(Map<String,Object>)login.get("account");
        assertEquals("Zaire",a.get("name"));assertEquals("185fb4a0-40e2-32d2-acaf-60e26d2869ba",a.get("id"));
    }
    @Test void ticketIsSingleUseAndIdentityBound() throws Exception {
        var session=register();var game=core.gameSession(field(session,"access_token"),"sinsworld.minecraft.best");String ticket=field(game,"ticket");
        assertFalse(core.redeem(ticket,"Other",AuthCore.offlineUuid("Other"),"sinsworld.minecraft.best"));
        assertFalse(core.redeem(ticket,"Zaire",UUID.randomUUID(),"sinsworld.minecraft.best"));
        assertTrue(core.redeem(ticket,"Zaire",AuthCore.offlineUuid("Zaire"),"sinsworld.minecraft.best"));
        assertFalse(core.redeem(ticket,"Zaire",AuthCore.offlineUuid("Zaire"),"sinsworld.minecraft.best"));
    }
    @Test void concurrentReplayHasExactlyOneWinner() throws Exception {
        var game=core.gameSession(field(register(),"access_token"),"server");String ticket=field(game,"ticket");
        var pool=Executors.newFixedThreadPool(8);
        try { var futures=new ArrayList<Future<Boolean>>();for(int i=0;i<16;i++)futures.add(pool.submit(()->core.redeem(ticket,"Zaire",AuthCore.offlineUuid("Zaire"),"server")));
            int winners=0;for(var f:futures)if(f.get())winners++;assertEquals(1,winners);
        }finally{pool.shutdownNow();}
    }
    @Test void expiredTicketAndAccessAreRejected() throws Exception {
        var session=register();var game=core.gameSession(field(session,"access_token"),"server");clock.millis+=610000;
        assertFalse(core.redeem(field(game,"ticket"),"Zaire",AuthCore.offlineUuid("Zaire"),"server"));
        clock.millis+=16*60000;assertThrows(AuthCore.Rejected.class,()->core.gameSession(field(session,"access_token"),"server"));
    }
    @Test void refreshRotatesAndSurvivesRestartWithoutPlaintextSecrets() throws Exception {
        var first=register();var second=core.refresh(field(first,"refresh_token"));
        assertThrows(AuthCore.Rejected.class,()->core.refresh(field(first,"refresh_token")));
        String stored=Files.readString(dir.resolve("state.json"));
        for(String key:List.of("access_token","refresh_token","recovery_code"))assertFalse(stored.contains(field(first,key)));
        core=new AuthCore(backend,clock,dir.resolve("state.json"));
        assertThrows(AuthCore.Rejected.class,()->core.refresh(field(first,"refresh_token")));
        assertNotNull(core.refresh(field(second,"refresh_token")));
    }
    @Test void networkTicketRejectsWrongTargetEvenWhenNodesShareAnAddress() throws Exception {
        var ticket=core.gameSession(field(register(),"access_token"),"same.example.org","naruto");String proof=field(ticket,"ticket");
        assertFalse(core.redeem(proof,"Zaire",AuthCore.offlineUuid("Zaire"),"solo"));
        assertTrue(core.redeem(proof,"Zaire",AuthCore.offlineUuid("Zaire"),"naruto"));
        assertFalse(core.redeem(proof,"Zaire",AuthCore.offlineUuid("Zaire"),"naruto"));
    }
    @Test void skinIdentityComesOnlyFromValidSession() throws Exception {
        var session=register();String token=field(session,"access_token");
        var identity=core.currentAccount(token);
        assertEquals("Zaire",identity.get("name"));
        assertEquals(AuthCore.offlineUuid("Zaire").toString(),identity.get("id"));
        assertThrows(AuthCore.Rejected.class,()->core.currentAccount("not-a-token"));
        core.logout(token);
        assertThrows(AuthCore.Rejected.class,()->core.currentAccount(token));
    }
    @Test void logoutRevokesTicketAndRefreshButNotAnotherDevice() throws Exception {
        var a=register();var b=core.login("Zaire",password);var game=core.gameSession(field(a,"access_token"),"server");core.logout(field(a,"access_token"));
        assertFalse(core.redeem(field(game,"ticket"),"Zaire",AuthCore.offlineUuid("Zaire"),"server"));
        assertThrows(AuthCore.Rejected.class,()->core.refresh(field(a,"refresh_token")));
        assertNotNull(core.gameSession(field(b,"access_token"),"server"));
    }
    @Test void passwordChangeAndTotpRevokePreviouslyIssuedProofs() throws Exception {
        var a=register();var game=core.gameSession(field(a,"access_token"),"server");backend.changePassword("Zaire","ChangedPassword123");
        assertFalse(core.redeem(field(game,"ticket"),"Zaire",AuthCore.offlineUuid("Zaire"),"server"));
        assertThrows(AuthCore.Rejected.class,()->core.refresh(field(a,"refresh_token")));
        backend.totp=true;assertThrows(AuthCore.Rejected.class,()->core.login("Zaire","ChangedPassword123"));
    }
    @Test void recoveryIsOneUseRevokesSessionsAndIssuesFreshCodeOnNextLogin() throws Exception {
        var a=register();String code=field(a,"recovery_code");
        assertThrows(AuthCore.Rejected.class,()->core.recover("Zaire","wrong","NewPassword123",12,30,Set.of()));
        core.recover("Zaire",code,"NewPassword123",12,30,Set.of());
        assertThrows(AuthCore.Rejected.class,()->core.refresh(field(a,"refresh_token")));
        core=new AuthCore(backend,clock,dir.resolve("state.json"));
        assertThrows(AuthCore.Rejected.class,()->core.recover("Zaire",code,"AnotherPassword123",12,30,Set.of()));
        assertNotEquals(code,core.login("Zaire","NewPassword123").get("recovery_code"));
    }
    @Test void rejectsDuplicateAndWeakRegistration() throws Exception {
        assertThrows(AuthCore.Rejected.class,()->core.register("Zaire","short",12,30,Set.of()));register();
        assertThrows(AuthCore.Rejected.class,()->register());
    }
}
