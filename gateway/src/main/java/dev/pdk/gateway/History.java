package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.gson.JsonArray;
import com.inductiveautomation.ignition.common.gson.JsonElement;
import com.inductiveautomation.ignition.common.gson.JsonObject;
import com.inductiveautomation.ignition.common.model.values.QualityCode;
import com.inductiveautomation.ignition.common.script.JythonExecException;
import com.inductiveautomation.ignition.common.script.ScriptManager;
import com.inductiveautomation.ignition.common.tags.model.TagPath;
import com.inductiveautomation.ignition.gateway.model.GatewayContext;
import org.python.core.Py;
import org.python.core.PyList;
import org.python.core.PyObject;
import org.python.core.PyStringMap;

import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Synthetic tag history for simulated tags: points (value, quality, timestamp) stored into a history provider for
 * tags under {@code [provider]PDK/}, so a trend or a tag-history binding has data to show. It goes through the
 * public scripting function {@code system.tag.storeTagHistory}, run by the gateway's script manager, rather than the
 * historian's internal API, which changes between versions.
 */
final class History {
    static final int MAX_POINTS = 10_000;
    private static final String STORE = "pdk_result = system.tag.storeTagHistory(*pdk_args)";

    private final GatewayContext context;
    private final Sandbox sandbox;
    private final String providerName;

    History(GatewayContext context, Sandbox sandbox) {
        this.context = context;
        this.sandbox = sandbox;
        this.providerName = Tags.providerName();
    }

    /** GET /history: the tag history providers of the gateway. */
    Reply providers() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("providers", providers(context));
        return Reply.ok(body);
    }

    private static List<String> providers(GatewayContext context) {
        try {
            List<String> names = context.getTagHistoryManager().getTagHistoryProviders();
            return names == null ? List.of() : names;
        } catch (RuntimeException | LinkageError error) {
            throw new ApiError(501, "HISTORY_UNAVAILABLE", "No tag history manager: " + error.getMessage());
        }
    }

    /**
     * POST /history {"provider": "...", "points": [{"path", "value", "timestamp" (ms), "quality"?}]}: stores the
     * points of tags under PDK/ into that history provider.
     */
    Reply store(String body) {
        sandbox.requireEnabled();
        JsonObject json = Tags.object(body);
        JsonElement providerElement = json.get("provider");
        if (providerElement == null || !providerElement.isJsonPrimitive()) {
            throw new ApiError(400, "BAD_REQUEST", "'provider' (a tag history provider) is required.");
        }
        String provider = providerElement.getAsString();
        List<String> known = providers(context);
        if (known.stream().noneMatch(name -> name.equalsIgnoreCase(provider))) {
            throw new ApiError(404, "HISTORY_PROVIDER_NOT_FOUND", "No tag history provider '" + provider
                    + "' (have: " + String.join(", ", known) + ").");
        }
        JsonElement items = json.get("points");
        if (items == null || !items.isJsonArray() || items.getAsJsonArray().isEmpty()
                || items.getAsJsonArray().size() > MAX_POINTS) {
            throw new ApiError(400, "BAD_REQUEST", "'points' must be an array of 1 to " + MAX_POINTS + " points.");
        }
        List<PyObject> paths = new ArrayList<>();
        List<PyObject> values = new ArrayList<>();
        List<PyObject> qualities = new ArrayList<>();
        List<PyObject> timestamps = new ArrayList<>();
        for (JsonElement item : items.getAsJsonArray()) {
            if (!item.isJsonObject() || !item.getAsJsonObject().has("path") || !item.getAsJsonObject().has("timestamp")) {
                throw new ApiError(400, "BAD_REQUEST", "Every point is {\"path\", \"value\", \"timestamp\", \"quality\"?}.");
            }
            JsonObject point = item.getAsJsonObject();
            TagPath path = Tags.parse(point.get("path").getAsString(), providerName);
            Tags.requireWork(path, providerName);
            QualityCode quality = point.has("quality") && !point.get("quality").isJsonNull()
                    ? Tags.quality(point.get("quality").getAsString()) : QualityCode.Good;
            long timestamp;
            try {
                timestamp = point.get("timestamp").getAsLong();
            } catch (RuntimeException error) {
                throw new ApiError(400, "BAD_REQUEST", "'timestamp' is milliseconds since the epoch.");
            }
            paths.add(Py.java2py(path.toStringPartial()));
            values.add(Py.java2py(Tags.value(point.get("value"))));
            qualities.add(Py.java2py(quality));
            timestamps.add(Py.java2py(new Date(timestamp)));
        }
        ScriptManager scripts = context.getScriptManager();
        PyStringMap locals = scripts.createLocalsMap();
        locals.__setitem__("pdk_args", new PyList(List.of(Py.java2py(provider), Py.java2py(providerName),
                new PyList(paths), new PyList(values), new PyList(qualities), new PyList(timestamps))));
        try {
            scripts.runCode(STORE, locals, "<pdk-history>");
        } catch (JythonExecException error) {
            throw new ApiError(500, "HISTORY_STORE_FAILED", "system.tag.storeTagHistory failed: " + error.getMessage());
        }
        PyObject result = locals.__finditem__("pdk_result");
        Map<String, Object> reply = new LinkedHashMap<>();
        reply.put("provider", provider);
        reply.put("tagProvider", providerName);
        reply.put("stored", paths.size());
        reply.put("result", result == null || result == Py.None ? null : result.toString());
        return Reply.ok(reply);
    }
}
