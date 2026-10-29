package world.sins.auth;
import com.google.gson.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class NetworkAndSkinChangesTest {
    @TempDir Path dir;
    @Test void nodeKeysAreIndependentPersistentAndCannotRedeemAsAnotherNode() throws Exception {
        Path keys=dir.resolve("keys.json");var addresses=Map.of("sinsworld","sinsworld.minecraft.best","naruto","naruto.example.org","solo","");
        var registry=new NetworkRegistry(addresses,keys);
        JsonObject secrets=JsonParser.parseString(Files.readString(keys)).getAsJsonObject();
        String naruto=secrets.get("naruto").getAsString(),sins=secrets.get("sinsworld").getAsString();
        assertNotEquals(naruto,sins);assertEquals("naruto.example.org",registry.authorize("naruto",naruto));
        assertThrows(AuthCore.Rejected.class,()->registry.authorize("sinsworld",naruto));
        assertThrows(AuthCore.Rejected.class,()->registry.authorize("unknown",naruto));
        assertThrows(AuthCore.Rejected.class,()->registry.address("solo"));
        assertEquals("naruto.example.org",new NetworkRegistry(addresses,keys).authorize("naruto",naruto));
    }
    @Test void rejectsPrivateUrlsMalformedImagesAndBadVariant() {
        for(String value:List.of("http://example.org/a.png","https://127.0.0.1/a.png","https://localhost/a.png","https://x.local/a.png","https://user@example.org/a.png")){
            var input=new JsonObject();input.addProperty("mode","url");input.addProperty("value",value);
            assertThrows(AuthCore.Rejected.class,()->SkinChangeInput.parse(input));
        }
        var png=new JsonObject();png.addProperty("mode","png");png.addProperty("value",Base64.getEncoder().encodeToString("not a skin".getBytes()));
        assertThrows(AuthCore.Rejected.class,()->SkinChangeInput.parse(png));
        var valid=new JsonObject();valid.addProperty("mode","name");valid.addProperty("value","Notch");assertEquals("Notch",SkinChangeInput.parse(valid).value());
        valid.addProperty("variant","invalid");assertThrows(AuthCore.Rejected.class,()->SkinChangeInput.parse(valid));
    }
    @Test void changesAreOwnedBoundedAndPersistOnlyAfterGeneration() throws Exception {
        String texture="https://textures.minecraft.net/texture/"+"a".repeat(64);
        SkinLookup fake=new SkinLookup(){public Optional<String> texture(UUID id,String name){return Optional.of(texture);}public String change(UUID id,String name,SkinChangeInput input){return texture;}};
        try(var changes=new SkinChanges(fake)) {
            UUID id=UUID.randomUUID();var job=changes.submit(id,"Zaire",new SkinChangeInput("name","Notch","classic",null));
            String key=(String)job.get("job");Map<String,Object> status=changes.status(id,key);
            for(int i=0;i<100&&status.get("state").equals("pending");i++){Thread.sleep(10);status=changes.status(id,key);}
            assertEquals("done",status.get("state"));assertEquals(texture,status.get("texture_url"));
            assertThrows(AuthCore.Rejected.class,()->changes.status(UUID.randomUUID(),key));
            assertThrows(AuthCore.Rejected.class,()->changes.submit(id,"Zaire",new SkinChangeInput("name","Other","classic",null)));
        }
    }
    @Test void networkClientRequiresPinnedTlsOriginAndNodeCredential() {
        assertThrows(IllegalArgumentException.class,()->new NetworkClient("http://example.org","","naruto","a".repeat(43)));
        assertThrows(IllegalArgumentException.class,()->new NetworkClient("https://example.org/path","","naruto","a".repeat(43)));
        assertThrows(IllegalArgumentException.class,()->new NetworkClient("https://example.org","","naruto","short"));
    }
}
