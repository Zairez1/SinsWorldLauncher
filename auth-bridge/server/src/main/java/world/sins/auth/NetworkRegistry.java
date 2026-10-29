package world.sins.auth;
import com.google.gson.*;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.SecureRandom;
import java.util.*;

final class NetworkRegistry {
    private final Map<String,String> addresses, keys=new HashMap<>();
    NetworkRegistry(Map<String,String> addresses,Path file) throws Exception {
        this.addresses=Map.copyOf(addresses);
        if(Files.exists(file)) {
            JsonObject root=JsonParser.parseString(Files.readString(file)).getAsJsonObject();
            root.entrySet().forEach(e->keys.put(e.getKey(),e.getValue().getAsString()));
        }
        boolean changed=false;var random=new SecureRandom();
        for(String id:addresses.keySet()) {
            if(!id.matches("[a-z0-9_-]{1,32}"))throw new IllegalArgumentException("Invalid network server id");
            String key=keys.get(id);
            if(key==null){byte[] b=new byte[32];random.nextBytes(b);keys.put(id,Base64.getUrlEncoder().withoutPadding().encodeToString(b));changed=true;}
            else if(!key.matches("[A-Za-z0-9_-]{43}"))throw new IllegalArgumentException("Invalid network node secret");
        }
        if(changed||!Files.exists(file)) {
            Files.createDirectories(file.toAbsolutePath().getParent());Path tmp=Files.createTempFile(file.toAbsolutePath().getParent(),"network-keys-",".tmp");
            try {try{Files.setPosixFilePermissions(tmp,PosixFilePermissions.fromString("rw-------"));}catch(UnsupportedOperationException ignored){}
                Files.writeString(tmp,new GsonBuilder().setPrettyPrinting().create().toJson(keys));
                try{Files.move(tmp,file,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);}catch(AtomicMoveNotSupportedException e){Files.move(tmp,file,StandardCopyOption.REPLACE_EXISTING);}
            }finally{Files.deleteIfExists(tmp);}
        }
    }
    String address(String id) {
        String address=addresses.get(id);
        if(address==null||!address.matches("[A-Za-z0-9.-]+(?::[0-9]{1,5})?"))throw new AuthCore.Rejected(404,"Сервер ещё не подключён к сети");
        return address;
    }
    String authorize(String id,String key) {
        if(!AuthCore.equal(keys.get(id),key))throw new AuthCore.Rejected(401,"Unknown server credential");
        return address(id);
    }
}
