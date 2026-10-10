package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.model.ApplicationScope;
import com.inductiveautomation.ignition.common.resourcecollection.LastModification;
import com.inductiveautomation.ignition.common.resourcecollection.Resource;
import com.inductiveautomation.ignition.common.resourcecollection.ResourceChangeContext;
import com.inductiveautomation.ignition.common.resourcecollection.ResourceFilter;
import com.inductiveautomation.ignition.common.resourcecollection.ResourceId;
import com.inductiveautomation.ignition.common.resourcecollection.ResourcePath;
import com.inductiveautomation.ignition.common.resourcecollection.ResourceType;
import com.inductiveautomation.ignition.common.resourcecollection.RuntimeResourceCollection;
import com.inductiveautomation.ignition.gateway.resourcecollection.ResourceCollectionLifecycle;
import com.inductiveautomation.ignition.gateway.resourcecollection.ResourceCollectionLifecycleFactory;
import com.inductiveautomation.ignition.gateway.resourcecollection.ResourceCollectionManager;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Predicate;

/**
 * In-memory log of Perspective resource changes in the sandbox, as the gateway applies them. Each event says
 * whether PDK made it (the push carried {@link Resources#ORIGIN}) or someone else did: a Designer save, a scan.
 */
final class ChangeLog extends ResourceCollectionLifecycleFactory<ChangeLog.Watch> {
    static final int CAPACITY = 1000;
    static final String PERSPECTIVE = "com.inductiveautomation.perspective";
    private static final List<String> TYPES = List.of("views", "page-config", "style-classes", "session-props");

    private final Sandbox sandbox;
    private final Deque<Map<String, Object>> events = new ArrayDeque<>();
    private long sequence;

    ChangeLog(ResourceCollectionManager manager, Sandbox sandbox) {
        super(manager);
        this.sandbox = sandbox;
    }

    @Override
    public Watch createLifecycle(RuntimeResourceCollection collection) {
        return new Watch(collection);
    }

    @Override
    protected ResourceFilter getResourceFilter() {
        ResourceFilter.Builder builder = ResourceFilter.newBuilder().setApplicationScope(ApplicationScope.ALL);
        TYPES.forEach(type -> builder.addResourceType(new ResourceType(PERSPECTIVE, type)));
        return builder.build();
    }

    @Override
    public Predicate<RuntimeResourceCollection> getResourceCollectionFilter() {
        return collection -> sandbox.name().map(collection.getName()::equals).orElse(false);
    }

    /** Events after `since`, oldest first, at most `limit`; `truncated` if older ones fell off the buffer. */
    synchronized Map<String, Object> since(long since, int limit) {
        List<Map<String, Object>> after = events.stream()
                .filter(event -> (long) event.get("seq") > since)
                .limit(Math.max(1, limit))
                .toList();
        long oldest = events.isEmpty() ? sequence + 1 : (long) events.peekFirst().get("seq");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("seq", sequence);
        result.put("watching", sandbox.name().orElse(null));
        result.put("truncated", since + 1 < oldest);
        result.put("events", after);
        return result;
    }

    synchronized void record(String project, String op, ResourcePath path, Object context,
                                     Optional<Resource> resource) {
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("seq", ++sequence);
        event.put("at", Instant.now().toString());
        event.put("project", project);
        event.put("op", op);
        event.put("type", path.getResourceType().moduleId() + "/" + path.getResourceType().typeId());
        event.put("path", path.getPath().toString());
        event.put("origin", context == Resources.ORIGIN ? "pdk" : "external");
        event.put("context", context == null ? null : context.getClass().getName());
        event.put("folder", resource.map(Resource::isFolder).orElse(null));
        event.put("actor", resource.flatMap(LastModification::of).map(LastModification::actor).orElse(null));
        event.put("signature", resource.map(Resources::signatureOf).orElse(null));
        events.addLast(event);
        while (events.size() > CAPACITY) {
            events.removeFirst();
        }
    }

    /** One watched project; the factory calls onBeforeChanges with the push context before each batch. */
    final class Watch extends ResourceCollectionLifecycle {
        private volatile Object context;

        Watch(RuntimeResourceCollection collection) {
            super(collection);
        }

        @Override
        protected void onStartup(List<Resource> resources) {
            // Only changes after startup are logged.
        }

        @Override
        protected void onShutdown(List<ResourceId> resources) {
            // Nothing to release.
        }

        @Override
        protected void onBeforeChanges(ResourceChangeContext change) {
            Object value = change.getContextObject().orElse(null);
            // The lifecycle factory passes the event's Optional<context> itself as the context object.
            context = value instanceof Optional<?> wrapped ? wrapped.orElse(null) : value;
        }

        @Override
        protected void onAfterChanges() {
            context = null;
        }

        @Override
        protected void onResourcesCreated(List<Resource> resources) {
            resources.forEach(resource -> record(name(), "created", resource.getResourcePath(), context,
                    Optional.of(resource)));
        }

        @Override
        protected void onResourcesModified(List<Resource> resources) {
            resources.forEach(resource -> record(name(), "modified", resource.getResourcePath(), context,
                    Optional.of(resource)));
        }

        @Override
        protected void onResourcesDeleted(List<ResourceId> resources) {
            resources.forEach(id -> record(name(), "deleted", id.getResourcePath(), context, Optional.empty()));
        }

        private String name() {
            return getResourceCollection().getName();
        }
    }
}
