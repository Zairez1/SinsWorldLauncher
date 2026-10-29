package world.sins.auth;
import java.lang.reflect.*;
import java.util.*;

/** Public API only, from the installed Java-compatible SkinsRestorer 15 plugin. */
final class SkinsRestorerLookup implements SkinLookup {
    private final Object api, players, skins;
    private final ClassLoader loader;
    private final Class<?> apiType, playerType, storageType, propertyType, variantType, identifierType;
    SkinsRestorerLookup(ClassLoader pluginLoader) throws ReflectiveOperationException {
        loader=pluginLoader;apiType=type("SkinsRestorer");
        api=type("SkinsRestorerProvider").getMethod("get").invoke(null);
        playerType=type("storage.PlayerStorage");storageType=type("storage.SkinStorage");
        propertyType=type("property.SkinProperty");variantType=type("property.SkinVariant");identifierType=type("property.SkinIdentifier");
        players=apiType.getMethod("getPlayerStorage").invoke(api);skins=apiType.getMethod("getSkinStorage").invoke(api);
        playerType.getMethod("getSkinForPlayer",UUID.class,String.class);
    }
    private Class<?> type(String name) throws ClassNotFoundException {return Class.forName("net.skinsrestorer.api."+name,true,loader);}
    private String url(Object p) throws Exception {return (String)type("PropertyUtils").getMethod("getSkinTextureUrl",propertyType).invoke(null,p);}
    private Optional<?> property(UUID id,String name) throws Exception {
        try{return (Optional<?>)playerType.getMethod("getSkinForPlayer",UUID.class,String.class,boolean.class).invoke(players,id,name,false);}
        catch(NoSuchMethodException e){return (Optional<?>)playerType.getMethod("getSkinForPlayer",UUID.class,String.class).invoke(players,id,name);}
    }
    @Override public Optional<String> texture(UUID id,String name) throws Exception {
        var p=property(id,name);return p.isPresent()?SkinTextures.canonical(url(p.get())):Optional.empty();
    }
    @Override public Map<String,String> signedSkin(UUID id,String name) throws Exception {
        var p=property(id,name);if(p.isEmpty())return Map.of();
        return Map.of("value",(String)propertyType.getMethod("getValue").invoke(p.get()),"signature",(String)propertyType.getMethod("getSignature").invoke(p.get()));
    }
    @SuppressWarnings({"unchecked","rawtypes"})
    @Override public String change(UUID id,String name,SkinChangeInput input) throws Exception {
        Object p,identifier;Object variant=Enum.valueOf((Class)variantType,input.variant().equals("slim")?"SLIM":"CLASSIC");
        if(input.mode().equals("png")) {
            Object mine=apiType.getMethod("getMineSkinAPI").invoke(api);
            Object result=type("connections.MineSkinAPI").getMethod("genSkin",byte[].class,variantType).invoke(mine,input.png(),variant);
            p=type("connections.model.MineSkinResponse").getMethod("getProperty").invoke(result);
            String custom="launcher_"+id.toString().replace("-", "");
            storageType.getMethod("setCustomSkinData",String.class,propertyType).invoke(skins,custom,p);
            identifier=identifierType.getMethod("ofCustom",String.class).invoke(null,custom);
        } else {
            Optional<?> found=input.mode().equals("name")?(Optional<?>)storageType.getMethod("findOrCreateSkinData",String.class).invoke(skins,input.value()):
                (Optional<?>)storageType.getMethod("findOrCreateSkinData",String.class,variantType).invoke(skins,input.value(),variant);
            if(found.isEmpty())throw new IllegalArgumentException("Skin not found");
            Class<?> result=type("property.InputDataResult");p=result.getMethod("getProperty").invoke(found.get());identifier=result.getMethod("getIdentifier").invoke(found.get());
        }
        playerType.getMethod("setSkinIdOfPlayer",UUID.class,identifierType).invoke(players,id,identifier);
        return SkinTextures.canonical(url(p)).orElseThrow();
    }
    @Override public void saveShared(UUID id,Map<String,String> signed) throws Exception {
        String value=signed.get("value"),signature=signed.get("signature");if(value==null||signature==null||value.length()>16000||signature.length()>4096)return;
        Object p=propertyType.getMethod("of",String.class,String.class).invoke(null,value,signature);
        String custom="launcher_"+id.toString().replace("-", "");storageType.getMethod("setCustomSkinData",String.class,propertyType).invoke(skins,custom,p);
        Object identifier=identifierType.getMethod("ofCustom",String.class).invoke(null,custom);
        playerType.getMethod("setSkinIdOfPlayer",UUID.class,identifierType).invoke(players,id,identifier);
    }
    @Override public void applyShared(Object player,Map<String,String> signed) throws Exception {
        if(signed.isEmpty())return;
        Object p=propertyType.getMethod("of",String.class,String.class).invoke(null,signed.get("value"),signed.get("signature"));
        Class<?> playerClass=Class.forName("org.bukkit.entity.Player",true,loader);
        Object applier=apiType.getMethod("getSkinApplier",Class.class).invoke(api,playerClass);
        type("property.SkinApplier").getMethod("applySkin",Object.class,propertyType).invoke(applier,player,p);
    }
}
