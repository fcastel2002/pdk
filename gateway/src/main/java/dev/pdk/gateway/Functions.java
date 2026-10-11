package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.expressions.FunctionFactory;
import com.inductiveautomation.ignition.common.gson.JsonArray;
import com.inductiveautomation.ignition.common.gson.JsonElement;
import com.inductiveautomation.ignition.common.gson.JsonObject;
import com.inductiveautomation.ignition.common.gson.JsonParser;
import com.inductiveautomation.ignition.common.script.PackageTreeNode;
import com.inductiveautomation.ignition.common.script.typing.CompletionDescriptor;
import com.inductiveautomation.ignition.gateway.model.GatewayContext;
import org.python.core.CompileMode;
import org.python.core.CompilerFlags;
import org.python.core.Py;
import org.python.core.PyException;
import org.python.core.PyObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * What code in a view may call, for the offline validator: the expression functions registered on the gateway
 * (with their category) and the scripting functions under {@code system.*} (from the gateway's scripting hints, the
 * tree the Designer's autocomplete uses). And a syntax check of Jython source with the gateway's own Jython: it is
 * compiled, never run.
 */
final class Functions {
    static final int MAX_SOURCES = 500;
    static final int MAX_SOURCE_CHARS = 200_000;
    private static final int MAX_DEPTH = 8;

    private final GatewayContext context;

    Functions(GatewayContext context) {
        this.context = context;
    }

    /** GET /functions: {"expression": [{"name", "category"}], "script": ["system.tag.readBlocking", ...]}. */
    Reply list() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("expression", expressionFunctions());
        body.put("script", scriptFunctions());
        return Reply.ok(body);
    }

    private List<Map<String, Object>> expressionFunctions() {
        FunctionFactory factory = context.getExpressionFunctionFactory();
        Map<String, String> categories = new TreeMap<>();
        for (String category : factory.getCategories()) {
            for (String name : factory.getFunctionsInCategory(category)) {
                categories.putIfAbsent(name, category);
            }
        }
        for (String name : factory.getFunctionNames()) {
            categories.putIfAbsent(name, null);
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map.Entry<String, String> entry : categories.entrySet()) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("name", entry.getKey());
            item.put("category", entry.getValue());
            result.add(item);
        }
        return result;
    }

    private List<String> scriptFunctions() {
        Set<String> names = new TreeSet<>();
        PackageTreeNode root = context.getScriptManager().getHintsTree();
        if (root != null) {
            walk(root, "", names, 0);
        }
        return new ArrayList<>(names);
    }

    private static void walk(PackageTreeNode node, String prefix, Set<String> names, int depth) {
        if (depth > MAX_DEPTH) {
            return;
        }
        for (CompletionDescriptor.Method method : node.methods()) {
            names.add(prefix + method.getName());
        }
        for (CompletionDescriptor.Attribute attribute : node.attributes()) {
            names.add(prefix + attribute.getName());
        }
        for (Map.Entry<String, PackageTreeNode> child : node.children().entrySet()) {
            names.add(prefix + child.getKey());
            walk(child.getValue(), prefix + child.getKey() + ".", names, depth + 1);
        }
    }

    /**
     * POST /scripts/compile {"sources": [{"id": "...", "code": "..."}]}: each source compiled as a module with the
     * gateway's Jython; a syntax error comes back with its line, column and message. Nothing is executed.
     */
    Reply compile(String body) {
        JsonArray sources = sources(body);
        List<Map<String, Object>> results = new ArrayList<>();
        for (JsonElement element : sources) {
            JsonObject source = element.getAsJsonObject();
            String id = source.has("id") && !source.get("id").isJsonNull() ? source.get("id").getAsString() : "";
            String code = source.get("code").getAsString();
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("id", id);
            try {
                Py.compile_flags(code, "<pdk>", CompileMode.exec, new CompilerFlags());
                result.put("ok", true);
            } catch (PyException error) {
                result.put("ok", false);
                describe(error, result);
            } catch (RuntimeException error) {
                result.put("ok", false);
                result.put("message", error.getClass().getSimpleName() + ": " + error.getMessage());
            }
            results.add(result);
        }
        return Reply.ok(Map.of("results", results));
    }

    private static void describe(PyException error, Map<String, Object> result) {
        boolean syntax = error.match(Py.SyntaxError);
        error.normalize();
        PyObject value = error.value;
        result.put("kind", syntax ? "SyntaxError" : String.valueOf(error.type));
        Integer line = intAttribute(value, "lineno");
        Integer column = intAttribute(value, "offset");
        if (line != null) {
            result.put("line", line);
        }
        if (column != null) {
            result.put("column", column);
        }
        PyObject message = value == null ? null : value.__findattr__("msg");
        result.put("message", message != null && message != Py.None ? message.toString() : String.valueOf(value));
    }

    private static Integer intAttribute(PyObject value, String name) {
        if (value == null) {
            return null;
        }
        try {
            PyObject attribute = value.__findattr__(name);
            return attribute == null || attribute == Py.None ? null : attribute.asInt();
        } catch (PyException error) {
            return null;
        }
    }

    private static JsonArray sources(String body) {
        JsonElement parsed;
        try {
            parsed = JsonParser.parseString(body == null ? "" : body);
        } catch (RuntimeException error) {
            throw new ApiError(400, "BAD_REQUEST", "The body is not JSON: " + error.getMessage());
        }
        if (!parsed.isJsonObject() || !parsed.getAsJsonObject().has("sources")
                || !parsed.getAsJsonObject().get("sources").isJsonArray()) {
            throw new ApiError(400, "BAD_REQUEST", "Expected {\"sources\": [{\"id\": \"...\", \"code\": \"...\"}]}.");
        }
        JsonArray sources = parsed.getAsJsonObject().getAsJsonArray("sources");
        if (sources.size() > MAX_SOURCES) {
            throw new ApiError(413, "TOO_MANY_SOURCES", "At most " + MAX_SOURCES + " sources per request.");
        }
        for (JsonElement element : sources) {
            if (!element.isJsonObject() || !element.getAsJsonObject().has("code")
                    || !element.getAsJsonObject().get("code").isJsonPrimitive()) {
                throw new ApiError(400, "BAD_REQUEST", "Every source is {\"id\": \"...\", \"code\": \"...\"}.");
            }
            if (element.getAsJsonObject().get("code").getAsString().length() > MAX_SOURCE_CHARS) {
                throw new ApiError(413, "SOURCE_TOO_LARGE", "A source is longer than " + MAX_SOURCE_CHARS + " chars.");
            }
        }
        return sources;
    }
}
