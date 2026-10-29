package world.sins.auth;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** Exercise the adapter against the actual Java-compatible 15.7.9 public API JAR. */
class SkinsRestorerPublicApiTest {
    Class<?> api(String name)throws Exception{return Class.forName("net.skinsrestorer.api."+name);}
    Object proxy(Class<?> type,InvocationHandler handler){return Proxy.newProxyInstance(getClass().getClassLoader(),new Class<?>[]{type},handler);}
    @Test void readsAndWritesSavedSkinsUsingTheRealPublicApi() throws Exception {
        Class<?> prop=api("property.SkinProperty"),variant=api("property.SkinVariant"),ident=api("property.SkinIdentifier"),result=api("property.InputDataResult");
        String url="https://textures.minecraft.net/texture/"+"a".repeat(64);
        String value=Base64.getEncoder().encodeToString(("{\"textures\":{\"SKIN\":{\"url\":\""+url+"\"}}}").getBytes(StandardCharsets.UTF_8));
        Object property=prop.getMethod("of",String.class,String.class).invoke(null,value,"signature");
        Object identifier=ident.getMethod("ofCustom",String.class).invoke(null,"test");
        Object data=result.getMethod("of",ident,prop).invoke(null,identifier,property);
        List<Object> saved=new ArrayList<>();
        Object players=proxy(api("storage.PlayerStorage"),(p,m,a)->{if(m.getName().equals("getSkinForPlayer"))return Optional.of(property);if(m.getName().equals("setSkinIdOfPlayer"))saved.add(a[1]);return null;});
        Object storage=proxy(api("storage.SkinStorage"),(p,m,a)->{if(m.getName().equals("findOrCreateSkinData"))return Optional.of(data);if(m.getName().equals("setCustomSkinData"))saved.add(a[0]);return null;});
        Object classic=variant.getEnumConstants()[0];
        Class<?> response=api("connections.model.MineSkinResponse");
        Object generated=response.getMethod("of",prop,String.class,variant,variant).invoke(null,property,"test",classic,classic);
        Object mine=proxy(api("connections.MineSkinAPI"),(p,m,a)->generated);
        Class<?> sr=api("SkinsRestorer");Object implementation=proxy(sr,(p,m,a)->switch(m.getName()){case "getPlayerStorage"->players;case "getSkinStorage"->storage;case "getMineSkinAPI"->mine;default->null;});
        Method setter=api("SkinsRestorerProvider").getMethod("setApi",sr);setter.invoke(null,implementation);
        try {
            var lookup=new SkinsRestorerLookup(getClass().getClassLoader());UUID id=UUID.randomUUID();
            assertEquals(url,lookup.texture(id,"Zaire").orElseThrow());
            assertEquals(value,lookup.signedSkin(id,"Zaire").get("value"));
            assertEquals(url,lookup.change(id,"Zaire",new SkinChangeInput("name","Notch","classic",null)));
            assertEquals(url,lookup.change(id,"Zaire",new SkinChangeInput("png","","slim",new byte[]{1,2})));
            lookup.saveShared(id,Map.of("value",value,"signature","signature"));
            assertTrue(saved.size()>=5);
        }finally{setter.invoke(null,new Object[]{null});}
    }
}
