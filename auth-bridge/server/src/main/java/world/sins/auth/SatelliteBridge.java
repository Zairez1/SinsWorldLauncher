package world.sins.auth;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import org.bukkit.Bukkit;
import org.bukkit.entity.Player;
import org.bukkit.plugin.Plugin;
import org.bukkit.event.*;
import org.bukkit.event.player.PlayerCommandPreprocessEvent;

final class SatelliteBridge implements Listener,AutoCloseable {
    private final SinsWorldAuth owner;
    private final NetworkClient central;
    private final SatelliteAccounts accounts;
    private final SkinLookup skins;
    private final Map<UUID,Long> attempts=new HashMap<>();
    private long attemptSweep;
    private final Set<UUID> pending=ConcurrentHashMap.newKeySet();
    private final ThreadPoolExecutor workers=new ThreadPoolExecutor(2,2,30,TimeUnit.SECONDS,new ArrayBlockingQueue<>(16),r->{Thread t=new Thread(r,"network-login");t.setDaemon(true);return t;});
    SatelliteBridge(SinsWorldAuth owner,Plugin authMe) throws Exception {
        this.owner=owner;
        var c=owner.getConfig();String certificate=Files.readString(Path.of(c.getString("primary-certificate","plugins/SinsWorldAuth/primary-certificate.pem")));
        central=new NetworkClient(c.getString("primary-api-url",""),certificate,c.getString("network-server-id",""),c.getString("network-token",""));
        accounts=new SatelliteAccounts(authMe,owner.getDataFolder().toPath().resolve("network-owned-accounts.json"));
        SkinLookup found=null;Plugin sr=Bukkit.getPluginManager().getPlugin("SkinsRestorer");
        if(sr!=null&&sr.isEnabled())try{found=new SkinsRestorerLookup(sr.getClass().getClassLoader());}catch(Exception|LinkageError ignored){}
        skins=found;Bukkit.getPluginManager().registerEvents(this,owner);
    }
    @EventHandler(priority=EventPriority.HIGHEST,ignoreCancelled=true)
    public void blockLocalAuthChanges(PlayerCommandPreprocessEvent event) {
        String command=event.getMessage().substring(1).split("\\s+",2)[0];
        var registered=Bukkit.getPluginCommand(command);
        if(registered!=null&&registered.getPlugin().getName().equalsIgnoreCase("AuthMe")) {
            event.setCancelled(true);event.getPlayer().sendMessage("Аккаунт общий для всех серверов. Войдите или измените пароль в лаунчере.");
        }
    }
    void receive(Player player,byte[] message) {
        if(message.length!=43)return;String ticket=new String(message,StandardCharsets.US_ASCII);
        if(!ticket.matches("[A-Za-z0-9_-]{43}"))return;
        UUID id=player.getUniqueId();String name=player.getName();
        synchronized(attempts){long now=System.currentTimeMillis();
            if(now-attemptSweep>30000){attempts.entrySet().removeIf(e->now-e.getValue()>60000);attemptSweep=now;}
            if(now-attempts.getOrDefault(id,0L)<5000||(attempts.size()>=10000&&!attempts.containsKey(id)))return;
            attempts.put(id,now);
        }
        if(!pending.add(id))return;
        try{workers.execute(()->{
            try{
                var answer=central.redeem(ticket,name,id);if(!answer.has("ok")||!answer.get("ok").getAsBoolean())return;
                accounts.ensureOwned(name);
                final Map<String,String> shared=new HashMap<>();
                if(skins!=null&&answer.has("skin"))try{var skin=answer.getAsJsonObject("skin");shared.put("value",skin.get("value").getAsString());shared.put("signature",skin.get("signature").getAsString());skins.saveShared(id,shared);}catch(Exception|LinkageError ignored){shared.clear();}
                Bukkit.getScheduler().runTaskLater(owner,()->{
                    if(!same(player,id,name))return;
                    try{if(!accounts.authenticated(player))accounts.login(player);
                        if(skins!=null&&!shared.isEmpty())try{skins.applyShared(player,shared);}catch(Exception|LinkageError ignored){}
                        Bukkit.getScheduler().runTaskLater(owner,()->{if(same(player,id,name))try{if(!accounts.authenticated(player))player.kickPlayer("Не удалось подтвердить вход. Откройте лаунчер и войдите заново.");}catch(Exception e){player.kickPlayer("Сервис аккаунтов недоступен");}},100L);
                    }catch(Exception e){player.kickPlayer("Сервис аккаунтов недоступен");}
                },10L);
            }catch(Exception e){Bukkit.getScheduler().runTask(owner,()->{if(same(player,id,name))player.kickPlayer("Не удалось подключить единый аккаунт. Проверьте центральный сервис и отсутствие старого локального аккаунта.");});}
            finally{pending.remove(id);}
        });}catch(RejectedExecutionException e){pending.remove(id);}
    }
    private boolean same(Player p,UUID id,String name){return p.isOnline()&&Bukkit.getPlayer(id)==p&&p.getUniqueId().equals(id)&&p.getName().equals(name);}
    @Override public void close(){workers.shutdownNow();}
}
