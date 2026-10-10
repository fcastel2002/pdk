package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.gson.JsonArray;
import com.inductiveautomation.ignition.common.gson.JsonElement;
import com.inductiveautomation.ignition.common.gson.JsonNull;
import com.inductiveautomation.ignition.common.gson.JsonObject;
import com.inductiveautomation.ignition.common.model.values.QualityCode;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Turns the qualified form of {@code PropertyTree.toJson()} into plain JSON. Each node carries
 * {@code "$": [kind, code | [code, message], timestamp]}; leaves keep their value in {@code "$v"} and arrays
 * their items in {@code "$a"}. Every node whose quality is not Good is collected with its property path (e.g. {@code text}, {@code style.color},
 * {@code items[0]}).
 */
final class Qualified {
    static final int MAX_ISSUES = 100;

    private Qualified() {
    }

    static JsonElement plain(JsonElement node, String prefix, List<Map<String, Object>> issues) {
        return walk(node, prefix, issues);
    }

    private static JsonElement walk(JsonElement node, String path, List<Map<String, Object>> issues) {
        if (node == null || node.isJsonNull()) {
            return JsonNull.INSTANCE;
        }
        if (node.isJsonArray()) {
            JsonArray result = new JsonArray();
            JsonArray array = node.getAsJsonArray();
            for (int i = 0; i < array.size(); i++) {
                result.add(walk(array.get(i), path + "[" + i + "]", issues));
            }
            return result;
        }
        if (!node.isJsonObject()) {
            return node;
        }
        JsonObject object = node.getAsJsonObject();
        if (object.has("$")) {
            collect(object.get("$"), path, issues);
        }
        if (object.has("$v")) {
            return walk(object.get("$v"), path, issues);
        }
        if (object.has("$a")) {
            return walk(object.get("$a"), path, issues);
        }
        JsonObject result = new JsonObject();
        for (Map.Entry<String, JsonElement> entry : object.entrySet()) {
            if (!"$".equals(entry.getKey())) {
                String child = path.isEmpty() ? entry.getKey() : path + "." + entry.getKey();
                result.add(entry.getKey(), walk(entry.getValue(), child, issues));
            }
        }
        return result;
    }

    private static void collect(JsonElement meta, String path, List<Map<String, Object>> issues) {
        if (issues == null || issues.size() >= MAX_ISSUES || meta == null || !meta.isJsonArray()) {
            return;
        }
        JsonArray array = meta.getAsJsonArray();
        if (array.size() < 2) {
            return;
        }
        JsonElement quality = array.get(1);
        int code;
        String message = null;
        try {
            if (quality.isJsonArray() && quality.getAsJsonArray().size() > 0) {
                code = quality.getAsJsonArray().get(0).getAsInt();
                if (quality.getAsJsonArray().size() > 1 && quality.getAsJsonArray().get(1).isJsonPrimitive()) {
                    message = quality.getAsJsonArray().get(1).getAsString();
                }
            } else if (quality.isJsonPrimitive() && quality.getAsJsonPrimitive().isNumber()) {
                code = quality.getAsInt();
            } else {
                return;
            }
        } catch (RuntimeException error) {
            return;
        }
        QualityCode resolved = QualityCode.getQualityFor(code);
        if (resolved == null || resolved.isGood()) {
            return;
        }
        Map<String, Object> issue = new LinkedHashMap<>();
        issue.put("path", path);
        issue.put("quality", resolved.getName());
        issue.put("level", String.valueOf(resolved.getLevel()));
        if (message != null && !message.isEmpty()) {
            issue.put("message", message.length() <= 1000 ? message : message.substring(0, 1000) + "…");
        }
        issues.add(issue);
    }
}
