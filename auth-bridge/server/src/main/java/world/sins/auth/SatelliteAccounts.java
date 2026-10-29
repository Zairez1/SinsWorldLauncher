package world.sins.auth;
import com.google.gson.*;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
import java.security.SecureRandom;
import org.bukkit.entity.Player;
import org.bukkit.plugin.Plugin;

/** AuthMe protects pre-login players. Only centrally verified, bridge-owned local records can log in. */
final class SatelliteAccounts {
    private final Object api;
    private final Class<?> type;
    private final Set<String> owned=new HashSet<>();
    private final Path file;
    SatelliteAccounts(Plugin authMe,Path file) throws Exception {
        var config=org.bukkit.configuration.file.YamlConfiguration.loadConfiguration(new java.io.File(authMe.getDataFolder(),"config.yml"));
        if(config.getBoolean("settings.sessions.enabled",false))throw new IllegalStateException("Disable AuthMe IP sessions on satellite servers");
        this.file=file;type=Class.forName("fr.xephi.authme.api.v3.AuthMeApi",true,authMe.getClass().getClassLoader());api=type.getMethod("getInstance").invoke(null);
        type.getMethod("isRegistered",String.class);type.getMethod("registerPlayer",String.class,String.class);type.getMethod("forceLogin",Player.class);
        if(Files.exists(file))for(JsonElement e:JsonParser.parseString(Files.readString(file)).getAsJsonArray())owned.add(e.getAsString());
    }
    private Object call(String name,Class<?>[] types,Object... args) throws Exception {return type.getMethod(name,types).invoke(api,args);}
    synchronized void ensureOwned(String name) throws Exception {
        String canonical=name.toLowerCase(Locale.ROOT);
        boolean registered=(boolean)call("isRegistered",new Class<?>[]{String.class},name);
        if(registered&&!owned.contains(canonical))throw new IllegalStateException("Existing local AuthMe account needs explicit migration; no account was overwritten");
        if(!registered){byte[] b=new byte[21];new SecureRandom().nextBytes(b);String random=Base64.getUrlEncoder().withoutPadding().encodeToString(b);
            if(!(boolean)call("registerPlayer",new Class<?>[]{String.class,String.class},name,random))throw new IllegalStateException("Local AuthMe registration failed");
            long until=System.nanoTime()+3_000_000_000L;while(!(boolean)call("isRegistered",new Class<?>[]{String.class},name)) {if(System.nanoTime()>until)throw new IllegalStateException("Local registration not confirmed");Thread.sleep(50);}
            owned.add(canonical);Files.createDirectories(file.toAbsolutePath().getParent());Path tmp=Files.createTempFile(file.toAbsolutePath().getParent(),"network-owned-",".tmp");
            try{Files.writeString(tmp,new Gson().toJson(owned));try{Files.move(tmp,file,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);}catch(AtomicMoveNotSupportedException e){Files.move(tmp,file,StandardCopyOption.REPLACE_EXISTING);}}finally{Files.deleteIfExists(tmp);}
        }
    }
    void login(Player p) throws Exception {call("forceLogin",new Class<?>[]{Player.class},p);}
    boolean authenticated(Player p) throws Exception {return (boolean)call("isAuthenticated",new Class<?>[]{Player.class},p);}
}
