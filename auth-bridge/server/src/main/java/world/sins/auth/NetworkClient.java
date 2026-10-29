package world.sins.auth;
import com.google.gson.*;
import java.net.URI;
import java.net.http.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.time.Duration;
import java.util.*;
import javax.net.ssl.*;

final class NetworkClient {
    private final HttpClient client;
    private final URI endpoint;
    private final String node,key;
    NetworkClient(String origin,String pem,String node,String key) throws Exception {
        URI u=URI.create(origin);
        if(!"https".equals(u.getScheme())||u.getHost()==null||u.getUserInfo()!=null||u.getRawQuery()!=null||u.getFragment()!=null||!(u.getPath().isEmpty()||u.getPath().equals("/")))throw new IllegalArgumentException("Primary API must be an HTTPS origin");
        if(!node.matches("[a-z0-9_-]{1,32}")||!key.matches("[A-Za-z0-9_-]{43}"))throw new IllegalArgumentException("Configure the node id and its private network token");
        this.node=node;this.key=key;endpoint=URI.create(origin.replaceAll("/$", "")+"/v1/network/redeem");
        var cert=CertificateFactory.getInstance("X.509").generateCertificate(new ByteArrayInputStream(pem.getBytes(StandardCharsets.US_ASCII)));
        ((java.security.cert.X509Certificate)cert).checkValidity();
        KeyStore trust=KeyStore.getInstance(KeyStore.getDefaultType());trust.load(null);trust.setCertificateEntry("primary",cert);
        TrustManagerFactory tm=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());tm.init(trust);
        SSLContext context=SSLContext.getInstance("TLS");context.init(null,tm.getTrustManagers(),null);
        SSLParameters params=new SSLParameters();params.setEndpointIdentificationAlgorithm("HTTPS");
        client=HttpClient.newBuilder().sslContext(context).sslParameters(params).followRedirects(HttpClient.Redirect.NEVER).connectTimeout(Duration.ofSeconds(10)).build();
    }
    JsonObject redeem(String ticket,String name,UUID id) throws Exception {
        String body=new Gson().toJson(Map.of("server_id",node,"ticket",ticket,"name",name,"uuid",id.toString()));
        var request=HttpRequest.newBuilder(endpoint).timeout(Duration.ofSeconds(15)).header("Content-Type","application/json").header("Authorization","Bearer "+key)
            .header("User-Agent","SinsWorldBridge/1.0").POST(HttpRequest.BodyPublishers.ofString(body)).build();
        var response=client.send(request,HttpResponse.BodyHandlers.ofInputStream());
        byte[] bytes;try(var in=response.body()){bytes=in.readNBytes(65537);}
        if(response.statusCode()!=200||bytes.length>65536)throw new IOException("Primary account verification unavailable");
        return JsonParser.parseString(new String(bytes,StandardCharsets.UTF_8)).getAsJsonObject();
    }
}
