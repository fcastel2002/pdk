package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.functional.FragileSupplier;
import com.inductiveautomation.ignition.common.gson.JsonElement;
import com.inductiveautomation.ignition.gateway.model.GatewayContext;
import com.inductiveautomation.perspective.common.api.PropertyKey;
import com.inductiveautomation.perspective.common.api.PropertyType;
import com.inductiveautomation.perspective.common.config.ComponentConfig;
import com.inductiveautomation.perspective.common.config.PropertyConfig;
import com.inductiveautomation.perspective.gateway.api.Component;
import com.inductiveautomation.perspective.gateway.api.PerspectiveContext;
import com.inductiveautomation.perspective.gateway.model.ComponentModel;
import com.inductiveautomation.perspective.gateway.model.PageModel;
import com.inductiveautomation.perspective.gateway.model.ViewModel;
import com.inductiveautomation.perspective.gateway.property.PropertyTree;
import com.inductiveautomation.perspective.gateway.session.InternalSession;
import com.inductiveautomation.perspective.gateway.session.PerspectiveSessionMonitor;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Read-only view of running Perspective sessions. Everything that touches Perspective internals
 * ({@link InternalSession}, {@link PerspectiveSessionMonitor}, the models) stays in this class; when it stops
 * linking, the routes answer 501 RUNTIME_UNAVAILABLE and the client falls back to DOM-only eyes.
 */
final class Live {
    private static final long QUEUE_TIMEOUT_MS = 5000;
    private static final PropertyType[] COMPONENT_SCOPES = {
        PropertyType.props, PropertyType.position, PropertyType.custom, PropertyType.meta};
    private static final PropertyType[] VIEW_SCOPES = {PropertyType.params, PropertyType.custom};

    private final GatewayContext context;

    Live(GatewayContext context) {
        this.context = context;
    }

    boolean available() {
        try {
            return monitor() != null;
        } catch (ApiError error) {
            return false;
        }
    }

    /** Client sessions of a project, with their pages and the view instances mounted on each. */
    Reply sessions(String project) {
        List<Object> sessions = new ArrayList<>();
        for (InternalSession session : monitor().getClientSessionsForProject(project)) {
            sessions.add(onQueue(session, () -> describe(session)));
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("project", project);
        body.put("sessions", sessions);
        return Reply.ok(body);
    }

    /**
     * One page, found by its id (the browser keeps it in sessionStorage "perspective-tab-id") among the client
     * sessions of the project, with the evaluated tree of every view mounted on it.
     */
    Reply page(String project, String pageId) {
        for (InternalSession session : monitor().getClientSessionsForProject(project)) {
            Optional<Map<String, Object>> found = onQueue(session, () -> session.findPage(pageId).map(page -> {
                Map<String, Object> body = new LinkedHashMap<>();
                body.put("project", project);
                body.put("session", session.getSessionId().toString());
                body.put("page", page.getId());
                body.put("connected", page.isConnected());
                List<Object> views = new ArrayList<>();
                for (ViewModel view : page.getViews()) {
                    views.add(view(view));
                }
                body.put("views", views);
                return body;
            }));
            if (found.isPresent()) {
                return Reply.ok(found.get());
            }
        }
        throw new ApiError(404, "PAGE_NOT_FOUND", "No page '" + pageId + "' in the sessions of " + project + ".");
    }

    /**
     * Close the client session that owns a page, the way system.perspective.closeSession does: a browser that goes
     * away only disconnects its session, which then holds a slot until it times out (Maker caps them).
     */
    Reply close(String project, String pageId) {
        for (InternalSession session : monitor().getClientSessionsForProject(project)) {
            if (onQueue(session, () -> session.findPage(pageId).isPresent())) {
                String id = session.getSessionId().toString();
                onQueue(session, () -> {
                    session.close("Closed by PDK after an inspection.");
                    return null;
                });
                Map<String, Object> body = new LinkedHashMap<>();
                body.put("project", project);
                body.put("closed", id);
                return Reply.ok(body);
            }
        }
        throw new ApiError(404, "PAGE_NOT_FOUND", "No page '" + pageId + "' in the sessions of " + project + ".");
    }

    private PerspectiveSessionMonitor monitor() {
        PerspectiveSessionMonitor monitor;
        try {
            monitor = PerspectiveContext.get(context).getSessionMonitor();
        } catch (RuntimeException | LinkageError error) {
            throw new ApiError(501, "RUNTIME_UNAVAILABLE",
                    "Perspective session API unavailable: " + error.getClass().getSimpleName() + ": " + error.getMessage());
        }
        if (monitor == null) {
            throw new ApiError(501, "RUNTIME_UNAVAILABLE", "Perspective has no session monitor.");
        }
        return monitor;
    }

    /** Runs on the session's queue, where the models may be read safely, and waits a bounded time. */
    private static <T> T onQueue(InternalSession session, FragileSupplier<T, RuntimeException> read) {
        try {
            return session.queue().runOrSubmit(read).get(QUEUE_TIMEOUT_MS, TimeUnit.MILLISECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new ApiError(503, "INTERRUPTED", "Interrupted while reading the session.");
        } catch (TimeoutException error) {
            throw new ApiError(504, "SESSION_BUSY", "The session queue did not run the read within "
                    + QUEUE_TIMEOUT_MS + " ms.");
        } catch (ExecutionException error) {
            Throwable cause = error.getCause();
            if (cause instanceof ApiError api) {
                throw api;
            }
            throw new ApiError(500, "RUNTIME_READ_FAILED", String.valueOf(cause));
        } catch (RuntimeException | LinkageError error) {
            if (error instanceof ApiError api) {
                throw api;
            }
            throw new ApiError(501, "RUNTIME_UNAVAILABLE", error.getClass().getSimpleName() + ": " + error.getMessage());
        }
    }

    private static Map<String, Object> describe(InternalSession session) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("id", session.getSessionId().toString());
        result.put("uptimeMs", session.getUptime(TimeUnit.MILLISECONDS));
        result.put("lastCommMs", session.getLastComm(TimeUnit.MILLISECONDS));
        List<Object> pages = new ArrayList<>();
        for (PageModel page : session.getPages()) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("id", page.getId());
            entry.put("created", page.getCreated());
            entry.put("connected", page.isConnected());
            List<Object> views = new ArrayList<>();
            for (ViewModel view : page.getViews()) {
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("instance", view.getId().toString());
                item.put("resourcePath", view.getId().resourcePath);
                item.put("mountPath", view.getId().mountPath);
                views.add(item);
            }
            entry.put("views", views);
            pages.add(entry);
        }
        result.put("pages", pages);
        return result;
    }

    private static Map<String, Object> view(ViewModel view) {
        Map<String, Object> result = new LinkedHashMap<>();
        String instance = view.getId().toString();
        result.put("instance", instance);
        result.put("resourcePath", view.getId().resourcePath);
        result.put("mountPath", view.getId().mountPath);
        List<Map<String, Object>> issues = new ArrayList<>();
        for (PropertyType scope : VIEW_SCOPES) {
            put(result, scope, view.getPropertyTreeOf(scope), issues);
        }
        if (!issues.isEmpty()) {
            result.put("qualities", issues);
        }
        ComponentConfig rootConfig = view.getConfig() == null ? null : view.getConfig().root;
        result.put("root", component(view.getRootContainer(), rootConfig, instance));
        return result;
    }

    private static Map<String, Object> component(Component component, ComponentConfig config, String instance) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("address", component.getComponentAddressPath());
        result.put("type", component.getType());
        result.put("name", component.getName());
        if (component instanceof ComponentModel model) {
            String qualified = model.getQualifiedPath();
            result.put("path", qualified != null && qualified.startsWith(instance + "/")
                    ? qualified.substring(instance.length() + 1) : qualified);
            List<Map<String, Object>> issues = new ArrayList<>();
            for (PropertyType scope : COMPONENT_SCOPES) {
                put(result, scope, model.getPropertyTreeOf(scope), issues);
            }
            if (!issues.isEmpty()) {
                result.put("qualities", issues);
            }
            Map<String, Object> bindings = bindings(config);
            if (!bindings.isEmpty()) {
                result.put("bindings", bindings);
            }
        }
        List<Object> children = new ArrayList<>();
        int index = 0;
        for (Component child : component.getChildren()) {
            ComponentConfig childConfig = config != null && config.children != null && index < config.children.size()
                    ? config.children.get(index) : null;
            children.add(component(child, childConfig, instance));
            index++;
        }
        if (!children.isEmpty()) {
            result.put("children", children);
        }
        return result;
    }

    private static void put(Map<String, Object> result, PropertyType scope, PropertyTree tree,
                            List<Map<String, Object>> issues) {
        if (tree == null) {
            return;
        }
        JsonElement plain = Qualified.plain(tree.toJson(), scope.name(), issues);
        if (!(plain.isJsonObject() && plain.getAsJsonObject().size() == 0)) {
            result.put(scope.name(), plain);
        }
    }

    /** Configured bindings by property key ("props.text"); values and qualities are in the trees. */
    private static Map<String, Object> bindings(ComponentConfig config) {
        Map<String, Object> result = new LinkedHashMap<>();
        if (config == null || config.propConfig == null) {
            return result;
        }
        for (Map.Entry<PropertyKey, PropertyConfig> entry : config.propConfig.configs.entrySet()) {
            PropertyConfig property = entry.getValue();
            if (property == null || property.binding == null) {
                continue;
            }
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("type", property.binding.type);
            item.put("enabled", property.hasEnabledBinding());
            item.put("transforms", property.binding.transforms == null ? 0 : property.binding.transforms.length);
            result.put(entry.getKey().scope.name() + "." + entry.getKey().path, item);
        }
        return result;
    }
}
