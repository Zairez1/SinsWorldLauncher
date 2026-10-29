package world.sins.auth;
import java.util.*;
import java.util.concurrent.*;

final class SkinChanges implements AutoCloseable {
    private record Job(String id,long started,CompletableFuture<String> result) {}
    private final Map<UUID,Job> jobs=new HashMap<>();
    private final SkinLookup lookup;
    private final ThreadPoolExecutor workers=new ThreadPoolExecutor(2,2,30,TimeUnit.SECONDS,new ArrayBlockingQueue<>(16),r->{Thread t=new Thread(r,"skinsrestorer-change");t.setDaemon(true);return t;});
    SkinChanges(SkinLookup lookup){this.lookup=lookup;}
    synchronized Map<String,Object> submit(UUID id,String name,SkinChangeInput input){
        if(lookup==null)throw new AuthCore.Rejected(503,"SkinsRestorer не подключён на сервере");
        long now=System.currentTimeMillis();jobs.entrySet().removeIf(e->e.getValue().result.isDone()&&now-e.getValue().started>600000);
        Job old=jobs.get(id);if(old!=null&&(!old.result.isDone()||now-old.started<60000))throw new AuthCore.Rejected(429,"Дождитесь завершения запроса. Менять скин можно раз в минуту");
        if(jobs.size()>=1024&&!jobs.containsKey(id))throw new AuthCore.Rejected(503,"Сервис скинов занят");
        var future=new CompletableFuture<String>();var job=new Job(UUID.randomUUID().toString(),now,future);
        try{workers.execute(()->{try{future.complete(lookup.change(id,name,input));}catch(Exception|LinkageError e){future.completeExceptionally(e);}});}catch(RejectedExecutionException e){throw new AuthCore.Rejected(503,"Сервис скинов занят");}
        jobs.put(id,job);return Map.of("job",job.id,"state","pending");
    }
    synchronized Map<String,Object> status(UUID id,String key){
        Job job=jobs.get(id);if(job==null||!job.id.equals(key))throw new AuthCore.Rejected(404,"Запрос скина не найден");
        if(!job.result.isDone())return Map.of("state","pending");
        if(job.result.isCompletedExceptionally())return Map.of("state","error","message","Не удалось подготовить скин. Проверьте ник/PNG и доступность MineSkin в SkinsRestorer");
        return Map.of("state","done","texture_url",job.result.join());
    }
    @Override public void close(){workers.shutdownNow();}
}
