package world.sins.auth;
import com.google.gson.JsonObject;
import java.net.URI;
import java.nio.ByteBuffer;
import java.util.*;

record SkinChangeInput(String mode,String value,String variant,byte[] png) {
    static SkinChangeInput parse(JsonObject body) {
        try {
            String mode=body.get("mode").getAsString(),value=body.get("value").getAsString();
            String variant=body.has("variant")?body.get("variant").getAsString():"classic";
            if(!variant.equals("classic")&&!variant.equals("slim"))throw new IllegalArgumentException();
            byte[] png=null;
            switch(mode) {
                case "name" -> {if(!value.matches("[A-Za-z0-9_]{3,16}"))throw new IllegalArgumentException();}
                case "url" -> {
                    if(value.length()>2048)throw new IllegalArgumentException();URI u=URI.create(value);String host=u.getHost();
                    if(!"https".equals(u.getScheme())||host==null||u.getUserInfo()!=null||u.getPort()!=-1||u.getFragment()!=null||!host.matches("(?i)[a-z0-9][a-z0-9.-]*\\.[a-z]{2,}")||host.endsWith(".local")||host.endsWith(".internal"))throw new IllegalArgumentException();
                }
                case "png" -> {
                    if(value.length()>350000)throw new IllegalArgumentException();png=Base64.getDecoder().decode(value);
                    byte[] magic={(byte)137,80,78,71,13,10,26,10};
                    if(png.length<33||png.length>262144||!Arrays.equals(magic,Arrays.copyOf(png,8))||ByteBuffer.wrap(png,8,4).getInt()!=13||ByteBuffer.wrap(png,12,4).getInt()!=0x49484452||ByteBuffer.wrap(png,16,4).getInt()!=64)throw new IllegalArgumentException();
                    int h=ByteBuffer.wrap(png,20,4).getInt();if(h!=32&&h!=64)throw new IllegalArgumentException();
                }
                default -> throw new IllegalArgumentException();
            }
            return new SkinChangeInput(mode,value,variant,png);
        }catch(RuntimeException e){throw new AuthCore.Rejected(400,"Нужен ник, публичная HTTPS-ссылка на PNG или PNG 64×64/64×32 до 256 КБ");}
    }
}
