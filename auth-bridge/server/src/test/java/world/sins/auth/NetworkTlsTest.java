package world.sins.auth;
import com.sun.net.httpserver.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.atomic.*;
import static org.junit.jupiter.api.Assertions.*;

class NetworkTlsTest {
    @TempDir Path dir;
    @Test void verifiesPinnedCertificateAndHostnameWithoutRedirectingNodeSecret() throws Exception {
        var ssl=DirectTls.create(dir,"127.0.0.1");String pem=Files.readString(dir.resolve("tls-certificate.pem"));
        var server=HttpsServer.create(new InetSocketAddress("127.0.0.1",0),0);server.setHttpsConfigurator(new HttpsConfigurator(ssl));
        AtomicBoolean redirect=new AtomicBoolean(false);AtomicInteger requests=new AtomicInteger();
        String token="a".repeat(43);
        server.createContext("/v1/network/redeem",request->{
            requests.incrementAndGet();assertEquals("Bearer "+token,request.getRequestHeaders().getFirst("Authorization"));
            if(redirect.get()){request.getResponseHeaders().set("Location","https://example.org/never-forward-credentials");request.sendResponseHeaders(302,-1);}
            else{byte[] bytes="{\"ok\":true,\"skin\":{}}".getBytes(StandardCharsets.UTF_8);request.sendResponseHeaders(200,bytes.length);request.getResponseBody().write(bytes);}
            request.close();
        });server.start();
        try{
            String origin="https://127.0.0.1:"+server.getAddress().getPort();
            var client=new NetworkClient(origin,pem,"naruto",token);
            assertTrue(client.redeem("b".repeat(43),"Zaire",AuthCore.offlineUuid("Zaire")).get("ok").getAsBoolean());
            var badHost=new NetworkClient(origin.replace("127.0.0.1","localhost"),pem,"naruto",token);
            assertThrows(Exception.class,()->badHost.redeem("b".repeat(43),"Zaire",AuthCore.offlineUuid("Zaire")));
            assertEquals(1,requests.get());
            redirect.set(true);assertThrows(java.io.IOException.class,()->client.redeem("b".repeat(43),"Zaire",AuthCore.offlineUuid("Zaire")));
            assertEquals(2,requests.get());
        }finally{server.stop(0);}
    }
}
