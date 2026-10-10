package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.gson.Gson;
import com.inductiveautomation.ignition.common.gson.GsonBuilder;
import com.inductiveautomation.ignition.common.resourcecollection.ResourceCollection;
import com.inductiveautomation.ignition.gateway.dataroutes.HttpMethod;
import com.inductiveautomation.ignition.gateway.dataroutes.RequestContext;
import com.inductiveautomation.ignition.gateway.dataroutes.RouteGroup;
import com.inductiveautomation.ignition.gateway.model.GatewayContext;
import jakarta.servlet.http.HttpServletResponse;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Pattern;

/** Routes under /data/pdk/v1. Every route goes through {@link Access}; writes also through {@link Sandbox}. */
final class Routes {
    static final String PREFIX = "/v1";
    private static final String RESOURCES = "/projects/:project/resources/*";
    private static final Pattern PROJECT_NAME = Pattern.compile(ResourceCollection.PROJECT_NAME_PATTERN);

    private final Access access;
    private final Health health;
    private final Resources resources;
    private final ChangeLog changes;
    private final Catalog catalog;
    private final Live live;
    private final LogTap logs;
    private final Tags tags;
    private final Gson gson = new GsonBuilder().serializeNulls().disableHtmlEscaping().create();

    Routes(GatewayContext context, Token token, Sandbox sandbox, ChangeLog changes, LogTap logs) {
        this.access = new Access(token);
        this.live = new Live(context);
        this.logs = logs;
        this.tags = new Tags(context, sandbox);
        this.health = new Health(context, sandbox, live, logs, tags);
        this.resources = new Resources(context.getProjectManager(), sandbox,
                context.getSystemManager().getDataDir().toPath().resolve("projects"));
        this.changes = changes;
        this.catalog = new Catalog(context);
    }

    void mount(RouteGroup routes) {
        mount(routes, HttpMethod.GET, "/health", request -> Reply.ok(health.describe()));
        mount(routes, HttpMethod.GET, RESOURCES, request -> resources.read(project(request), target(request)));
        mount(routes, HttpMethod.PUT, RESOURCES,
                request -> resources.write(project(request), target(request), request.readBody()));
        mount(routes, HttpMethod.DELETE, RESOURCES,
                request -> resources.delete(project(request), target(request), request.getParameter("signature")));
        mount(routes, HttpMethod.POST, "/scan", request -> resources.scan());
        mount(routes, HttpMethod.GET, "/changes", request -> Reply.ok(changes.since(
                number(request, "since", 0), (int) number(request, "limit", 200))));
        mount(routes, HttpMethod.GET, "/catalog",
                request -> catalog.list("true".equalsIgnoreCase(request.getParameter("full"))));
        mount(routes, HttpMethod.GET, "/catalog/:id", request -> catalog.one(request.getParameter("id")));
        mount(routes, HttpMethod.GET, "/sessions", request -> live.sessions(project(request)));
        mount(routes, HttpMethod.GET, "/pages/:page", request -> live.page(project(request), request.getParameter("page")));
        mount(routes, HttpMethod.GET, "/logs", request -> Reply.ok(logs.since(number(request, "since", 0),
                (int) number(request, "limit", 200), optionalProject(request), request.getParameter("view"))));
        mount(routes, HttpMethod.GET, "/tags",
                request -> tags.browse(request.getParameter("path"), number(request, "depth", 1)));
        mount(routes, HttpMethod.PUT, "/tags", request -> tags.importJson(request.getParameter("path"), request.readBody()));
        mount(routes, HttpMethod.DELETE, "/tags", request -> tags.delete(request.getParameter("path")));
        mount(routes, HttpMethod.POST, "/tags/read", request -> tags.read(request.readBody()));
        mount(routes, HttpMethod.POST, "/tags/write", request -> tags.write(request.readBody()));
        mount(routes, HttpMethod.GET, "/alarms", request -> tags.alarms(request.getParameter("path")));
        mount(routes, HttpMethod.POST, "/alarms/ack", request -> tags.acknowledge(request.getParameter("path")));
    }

    private void mount(RouteGroup routes, HttpMethod method, String path, Handler handler) {
        routes.newRoute(PREFIX + path)
                .method(method)
                .type(RouteGroup.TYPE_JSON)
                .accessControl(access::check)
                .handler((request, response) -> respond(handler, request, response))
                .renderer(gson::toJson)
                .nocache()
                .mount();
    }

    private static Object respond(Handler handler, RequestContext request, HttpServletResponse response) {
        try {
            Reply reply = handler.handle(request);
            response.setStatus(reply.status());
            return reply.body();
        } catch (ApiError error) {
            response.setStatus(error.status);
            return error.body();
        } catch (IOException | RuntimeException error) {
            GatewayHook.LOG.warn("PDK route failed: {} {}", request.getMethod(), request.getPath(), error);
            response.setStatus(HttpServletResponse.SC_INTERNAL_SERVER_ERROR);
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("error", "INTERNAL");
            body.put("message", error.getClass().getSimpleName() + ": " + error.getMessage());
            return body;
        }
    }

    private static long number(RequestContext request, String name, long fallback) {
        String value = request.getParameter(name);
        if (value == null || value.isBlank()) {
            return fallback;
        }
        try {
            return Long.parseLong(value.strip());
        } catch (NumberFormatException error) {
            throw new ApiError(400, "BAD_REQUEST", "'" + name + "' must be an integer, got '" + value + "'.");
        }
    }

    private static String project(RequestContext request) {
        String project = request.getParameter("project");
        if (project == null || !PROJECT_NAME.matcher(project).matches()) {
            throw new ApiError(400, "INVALID_PROJECT", "Invalid project name '" + project + "'.");
        }
        return project;
    }

    private static String optionalProject(RequestContext request) {
        return request.getParameter("project") == null ? null : project(request);
    }

    private static Target target(RequestContext request) {
        String path = request.getRequestUri().getPath();
        String marker = "/projects/" + project(request) + "/resources/";
        int index = path.indexOf(marker);
        if (index < 0) {
            throw new ApiError(400, "INVALID_PATH", "Missing resource path in '" + path + "'.");
        }
        return Target.parse(path.substring(index + marker.length()));
    }

    @FunctionalInterface
    private interface Handler {
        Reply handle(RequestContext request) throws IOException;
    }
}
