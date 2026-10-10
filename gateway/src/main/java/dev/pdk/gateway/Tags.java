package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.QualifiedPath;
import com.inductiveautomation.ignition.common.alarming.AlarmEvent;
import com.inductiveautomation.ignition.common.alarming.AlarmFilter;
import com.inductiveautomation.ignition.common.alarming.EventData;
import com.inductiveautomation.ignition.common.alarming.config.CommonAlarmProperties;
import com.inductiveautomation.ignition.common.browsing.BrowseFilter;
import com.inductiveautomation.ignition.common.browsing.Results;
import com.inductiveautomation.ignition.common.gson.JsonArray;
import com.inductiveautomation.ignition.common.gson.JsonElement;
import com.inductiveautomation.ignition.common.gson.JsonObject;
import com.inductiveautomation.ignition.common.gson.JsonParser;
import com.inductiveautomation.ignition.common.gson.JsonPrimitive;
import com.inductiveautomation.ignition.common.model.values.BasicQualifiedValue;
import com.inductiveautomation.ignition.common.model.values.QualifiedValue;
import com.inductiveautomation.ignition.common.model.values.QualityCode;
import com.inductiveautomation.ignition.common.tags.browsing.NodeDescription;
import com.inductiveautomation.ignition.common.tags.config.BasicTagConfiguration;
import com.inductiveautomation.ignition.common.tags.config.CollisionPolicy;
import com.inductiveautomation.ignition.common.tags.config.TagConfiguration;
import com.inductiveautomation.ignition.common.tags.config.types.TagObjectType;
import com.inductiveautomation.ignition.common.tags.model.SecurityContext;
import com.inductiveautomation.ignition.common.tags.model.TagPath;
import com.inductiveautomation.ignition.common.tags.model.TagProvider;
import com.inductiveautomation.ignition.common.tags.paths.parser.TagPathParser;
import com.inductiveautomation.ignition.gateway.model.GatewayContext;

import java.io.IOException;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Simulated tags (F5 part B): memory tags under {@code [provider]PDK/} of the provider {@code -Dpdk.tagProvider}
 * (default {@code default}). Reads and browses take any path; imports, writes and deletes need the sandbox and a
 * path under {@code PDK/}. Imports take Ignition's own tag export JSON, restricted to folders and memory tags with
 * nothing that runs or notifies on its own (event scripts, pipelines, bindings, history).
 */
final class Tags {
    static final String PROVIDER_PROPERTY = "pdk.tagProvider";
    static final String DEFAULT_PROVIDER = "default";
    static final String FOLDER = Sandbox.WORK_FOLDER;
    static final int MAX_IMPORT = 500;
    static final int MAX_NODES = 500;
    static final int MAX_DEPTH = 10;
    static final int MAX_ITEMS = 100;
    private static final long TIMEOUT_MS = 5000;
    private static final Set<String> TAG_TYPES = Set.of("Folder", "AtomicTag");
    private static final Set<String> FORBIDDEN_KEYS = Set.of(
            "eventScripts", "activePipeline", "clearPipeline", "ackPipeline", "bindType", "historyProvider");

    private final GatewayContext context;
    private final Sandbox sandbox;
    private final String providerName;

    Tags(GatewayContext context, Sandbox sandbox) {
        this.context = context;
        this.sandbox = sandbox;
        this.providerName = providerName();
    }

    static String providerName() {
        String value = System.getProperty(PROVIDER_PROPERTY);
        return value == null || value.isBlank() ? DEFAULT_PROVIDER : value.strip();
    }

    /** For Health: the provider name and whether it exists. */
    Map<String, Object> describe() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("provider", providerName);
        result.put("folder", FOLDER);
        boolean exists;
        try {
            exists = context.getTagManager().getTagProvider(providerName) != null;
        } catch (RuntimeException | LinkageError error) {
            exists = false;
        }
        result.put("providerExists", exists);
        return result;
    }

    /** GET /tags?path=&depth=: a flat list of the nodes under a folder, with value and quality. */
    Reply browse(String rawPath, long depth) {
        TagPath path = parse(rawPath == null || rawPath.isBlank() ? FOLDER : rawPath, providerName);
        TagProvider provider = provider(path.getSource());
        int levels = (int) Math.max(1, Math.min(MAX_DEPTH, depth));
        List<Map<String, Object>> nodes = new ArrayList<>();
        boolean truncated = browseInto(provider, path, levels, nodes, true);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("path", path.toStringFull());
        body.put("nodes", nodes);
        if (truncated) {
            body.put("truncated", true);
        }
        return Reply.ok(body);
    }

    /** POST /tags/read {"paths": [...]}: value, quality and timestamp of each path, any provider. */
    Reply read(String body) {
        List<String> raw = strings(object(body), "paths");
        List<TagPath> paths = new ArrayList<>();
        for (String item : raw) {
            paths.add(parse(item, providerName));
        }
        List<QualifiedValue> values = await(context.getTagManager().readAsync(paths, SecurityContext.systemContext()));
        List<Map<String, Object>> results = new ArrayList<>();
        for (int i = 0; i < paths.size(); i++) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("path", paths.get(i).toStringFull());
            putValue(entry, i < values.size() ? values.get(i) : null);
            results.add(entry);
        }
        return Reply.ok(Map.of("results", results));
    }

    /** PUT /tags?path=: imports tag export JSON into a folder under PDK/, creating the folders on the way. */
    Reply importJson(String rawPath, String body) {
        sandbox.requireEnabled();
        TagPath base = parse(rawPath == null || rawPath.isBlank() ? FOLDER : rawPath, providerName);
        requireWork(base, providerName);
        int count = checkImport(body);
        TagProvider provider = provider(base.getSource());
        ensureFolders(provider, base);
        List<QualityCode> qualities = await(provider.importTagsAsync(
                base, body, "json", CollisionPolicy.Overwrite, SecurityContext.systemContext()));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("path", base.toStringFull());
        result.put("tags", count);
        result.put("qualities", summarize(qualities));
        return Reply.ok(result);
    }

    /** POST /tags/write {"writes": [{"path", "value", "quality"?}]}: writes, then reads back. */
    Reply write(String body) {
        sandbox.requireEnabled();
        JsonElement items = object(body).get("writes");
        if (items == null || !items.isJsonArray() || items.getAsJsonArray().size() == 0
                || items.getAsJsonArray().size() > MAX_ITEMS) {
            throw new ApiError(400, "BAD_REQUEST", "'writes' must be an array of 1 to " + MAX_ITEMS + " objects.");
        }
        List<TagPath> paths = new ArrayList<>();
        List<QualifiedValue> values = new ArrayList<>();
        for (JsonElement item : items.getAsJsonArray()) {
            if (!item.isJsonObject() || !item.getAsJsonObject().has("path")) {
                throw new ApiError(400, "BAD_REQUEST", "Each write is {\"path\", \"value\", \"quality\"?}.");
            }
            JsonObject write = item.getAsJsonObject();
            TagPath path = parse(string(write.get("path"), "path"), providerName);
            requireWork(path, providerName);
            paths.add(path);
            JsonElement quality = write.get("quality");
            values.add(new BasicQualifiedValue(value(write.get("value")),
                    quality == null || quality.isJsonNull() ? QualityCode.Good : quality(string(quality, "quality"))));
        }
        // All paths come from one provider: requireWork pins them to providerName.
        TagProvider provider = provider(providerName);
        List<QualityCode> written = await(provider.writeAsync(paths, values, SecurityContext.systemContext()));
        List<QualifiedValue> readBack = await(provider.readAsync(paths, SecurityContext.systemContext()));
        List<Map<String, Object>> results = new ArrayList<>();
        for (int i = 0; i < paths.size(); i++) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("path", paths.get(i).toStringFull());
            entry.put("write", i < written.size() ? name(written.get(i)) : null);
            putValue(entry, i < readBack.size() ? readBack.get(i) : null);
            results.add(entry);
        }
        return Reply.ok(Map.of("results", results));
    }

    /** DELETE /tags?path=: removes a tag or folder under PDK/ (or PDK/ itself). */
    Reply delete(String rawPath) {
        sandbox.requireEnabled();
        if (rawPath == null || rawPath.isBlank()) {
            throw new ApiError(400, "BAD_REQUEST", "'path' is required.");
        }
        TagPath path = parse(rawPath, providerName);
        requireWork(path, providerName);
        // A mutable list: the provider sorts it (List.of fails with UnsupportedOperationException, Error_Exception).
        List<QualityCode> qualities = await(provider(path.getSource())
                .removeTagConfigsAsync(new ArrayList<>(List.of(path)), SecurityContext.systemContext()));
        QualityCode quality = qualities.isEmpty() ? null : qualities.get(0);
        if (quality != null && !quality.isGood()) {
            throw new ApiError(500, "TAG_DELETE_FAILED", "Deleting '" + path.toStringFull() + "' returned "
                    + name(quality) + "; see the gateway log (tags.management.provider).");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("path", path.toStringFull());
        result.put("result", name(quality));
        return Reply.ok(result);
    }

    /** GET /alarms?path=: alarm events whose source tag is at or under the path (default PDK/). */
    Reply alarms(String rawPath) {
        TagPath path = parse(rawPath == null || rawPath.isBlank() ? FOLDER : rawPath, providerName);
        List<Map<String, Object>> events = new ArrayList<>();
        boolean truncated = false;
        for (AlarmEvent event : events(path)) {
            if (events.size() >= MAX_NODES) {
                truncated = true;
                break;
            }
            QualifiedPath source = event.getSource();
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("tag", "[" + source.getPathComponent("prov") + "]" + source.getPathComponent("tag"));
            entry.put("name", event.getName());
            entry.put("state", event.getState() == null ? null : event.getState().name());
            entry.put("priority", event.getPriority() == null ? null : event.getPriority().name());
            entry.put("displayPath", event.getDisplayPath() == null ? null : event.getDisplayPath().toString());
            entry.put("label", event.getLabel());
            entry.put("acked", event.isAcked());
            entry.put("cleared", event.isCleared());
            entry.put("shelved", event.isShelved());
            events.add(entry);
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("path", path.toStringFull());
        body.put("alarms", events);
        if (truncated) {
            body.put("truncated", true);
        }
        return Reply.ok(body);
    }

    /** POST /alarms/ack?path=: acknowledges the unacknowledged alarm events of the tags under a path in PDK/. */
    Reply acknowledge(String rawPath) {
        sandbox.requireEnabled();
        TagPath path = parse(rawPath == null || rawPath.isBlank() ? FOLDER : rawPath, providerName);
        requireWork(path, providerName);
        List<UUID> ids = new ArrayList<>();
        for (AlarmEvent event : events(path)) {
            if (!event.isAcked()) {
                ids.add(event.getId());
            }
        }
        int failed = 0;
        if (!ids.isEmpty()) {
            EventData data = new EventData(System.currentTimeMillis());
            data.set(CommonAlarmProperties.AckUserName, "pdk");
            data.set(CommonAlarmProperties.AckNotes, "PDK: simulated tags reset");
            // acknowledgeBulk returns the ids it could not acknowledge (seen live: an empty set acks them all).
            failed = context.getAlarmManager().acknowledgeBulk(ids, data).size();
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("path", path.toStringFull());
        body.put("acknowledged", ids.size() - failed);
        body.put("failed", failed);
        return Reply.ok(body);
    }

    /** Alarm events whose source tag is at or under the path, in its provider. */
    private List<AlarmEvent> events(TagPath path) {
        List<AlarmEvent> found = new ArrayList<>();
        for (AlarmEvent event : context.getAlarmManager().queryStatus(new AlarmFilter())) {
            QualifiedPath source = event.getSource();
            String provider = source == null ? null : source.getPathComponent("prov");
            String tag = source == null ? null : source.getPathComponent("tag");
            if (provider != null && tag != null && provider.equalsIgnoreCase(path.getSource())
                    && under(tag, path.toStringPartial())) {
                found.add(event);
            }
        }
        return found;
    }

    // --- Pure checks, covered by SelfTest -------------------------------------------------------------------

    /** A tag path; without "[provider]" it hangs from the PDK provider. */
    static TagPath parse(String raw, String provider) {
        String text = raw == null ? "" : raw.strip();
        if (text.isEmpty()) {
            throw new ApiError(400, "INVALID_TAG_PATH", "Empty tag path.");
        }
        String full = text.startsWith("[") ? text : "[" + provider + "]" + text;
        TagPath path;
        try {
            path = TagPathParser.parse(full);
        } catch (IOException | RuntimeException error) {
            throw new ApiError(400, "INVALID_TAG_PATH", "Invalid tag path '" + raw + "': " + error.getMessage());
        }
        if (path == null || TagPathParser.isRelativePath(path) || path.getSource() == null
                || path.getSource().isBlank()) {
            throw new ApiError(400, "INVALID_TAG_PATH", "Tag path '" + raw + "' must be absolute.");
        }
        return path;
    }

    /** Writes stay in [provider]PDK/: no other provider, folder, relative segment or config property. */
    static void requireWork(TagPath path, String provider) {
        boolean inFolder = path.getSource().equalsIgnoreCase(provider) && path.getPathLength() >= 1
                && FOLDER.equalsIgnoreCase(path.getPathComponent(0)) && path.getProperty() == null;
        for (int i = 0; inFolder && i < path.getPathLength(); i++) {
            String part = path.getPathComponent(i);
            inFolder = part != null && !part.isBlank() && !part.equals(".") && !part.equals("..");
        }
        if (!inFolder) {
            throw new ApiError(403, "OUTSIDE_TAG_FOLDER", "Tag writes go only to [" + provider + "]" + FOLDER
                    + "/ (no properties), not '" + path.toStringFull() + "'.");
        }
    }

    /** Folder at or under another, by segments and case-insensitively, as Ignition resolves tag paths. */
    static boolean under(String tag, String folder) {
        String a = tag.toLowerCase(Locale.ROOT);
        String b = folder.toLowerCase(Locale.ROOT);
        return b.isEmpty() || a.equals(b) || a.startsWith(b + "/");
    }

    /**
     * Tag export JSON that may be imported: one tag object, or {"tags": [...]}; only folders and memory tags, no
     * event scripts, pipelines, bindings or history, names without '/' or '['. Returns the number of tags.
     */
    static int checkImport(String body) {
        JsonObject root = object(body);
        int[] count = {0};
        if (root.has("tagType")) {
            checkNode(root, "", count);
        } else {
            if (root.size() != 1 || !root.has("tags")) {
                throw new ApiError(400, "INVALID_TAGS", "The body is one tag object or {\"tags\": [...]}.");
            }
            checkChildren(root.get("tags"), "", count);
        }
        if (count[0] > MAX_IMPORT) {
            throw new ApiError(413, "TOO_MANY_TAGS",
                    count[0] + " tags in one import; at most " + MAX_IMPORT + " (Maker runs 10,000 per gateway).");
        }
        return count[0];
    }

    private static void checkChildren(JsonElement tags, String where, int[] count) {
        if (tags == null || !tags.isJsonArray()) {
            throw new ApiError(400, "INVALID_TAGS", "'tags' must be an array at '" + where + "'.");
        }
        for (JsonElement child : tags.getAsJsonArray()) {
            if (!child.isJsonObject()) {
                throw new ApiError(400, "INVALID_TAGS", "Every item of 'tags' must be an object at '" + where + "'.");
            }
            checkNode(child.getAsJsonObject(), where, count);
        }
    }

    private static void checkNode(JsonObject node, String parent, int[] count) {
        JsonElement nameElement = node.get("name");
        String name = nameElement != null && nameElement.isJsonPrimitive() ? nameElement.getAsString() : "";
        String where = parent.isEmpty() ? name : parent + "/" + name;
        if (name.isBlank() || name.contains("/") || name.contains("[") || name.contains("]") || name.equals(".") || name.equals("..")) {
            throw new ApiError(400, "INVALID_TAGS", "Invalid tag name '" + name + "' under '" + parent + "'.");
        }
        JsonElement typeElement = node.get("tagType");
        String type = typeElement != null && typeElement.isJsonPrimitive() ? typeElement.getAsString() : null;
        if (!TAG_TYPES.contains(type)) {
            throw new ApiError(400, "INVALID_TAGS", "'" + where + "': tagType must be Folder or AtomicTag, not "
                    + type + ".");
        }
        forbidden(node, where);
        if ("Folder".equals(type)) {
            if (node.has("tags")) {
                checkChildren(node.get("tags"), where, count);
            }
            return;
        }
        JsonElement source = node.get("valueSource");
        if (source == null || !source.isJsonPrimitive() || !"memory".equals(source.getAsString())) {
            throw new ApiError(400, "INVALID_TAGS", "'" + where + "': only memory tags (\"valueSource\": \"memory\").");
        }
        if (node.has("tags")) {
            throw new ApiError(400, "INVALID_TAGS", "'" + where + "': an AtomicTag has no 'tags'.");
        }
        count[0]++;
    }

    /** Keys that run or notify on their own, anywhere in the node except its child tags. */
    private static void forbidden(JsonElement element, String where) {
        if (element.isJsonArray()) {
            for (JsonElement item : element.getAsJsonArray()) {
                forbidden(item, where);
            }
        } else if (element.isJsonObject()) {
            for (Map.Entry<String, JsonElement> entry : element.getAsJsonObject().entrySet()) {
                String key = entry.getKey();
                JsonElement value = entry.getValue();
                if (FORBIDDEN_KEYS.contains(key) || ("historyEnabled".equals(key) && !isFalse(value))) {
                    throw new ApiError(400, "INVALID_TAGS", "'" + where + "': '" + key
                            + "' is not allowed in simulated tags.");
                }
                if (!"tags".equals(key)) {
                    forbidden(value, where);
                }
            }
        }
    }

    private static boolean isFalse(JsonElement value) {
        return value.isJsonPrimitive() && value.getAsJsonPrimitive().isBoolean() && !value.getAsBoolean();
    }

    /** A quality by name (Good, Bad_Stale, Bad_Disabled, Uncertain_LastKnownValue, ...). */
    static QualityCode quality(String name) {
        QualityCode code = QualityCode.getQualityFor(name);
        if (code == null || !code.getName().equalsIgnoreCase(name)) {
            throw new ApiError(400, "UNKNOWN_QUALITY", "Unknown quality '" + name + "'.");
        }
        return code;
    }

    /** A JSON scalar as a tag value: integers as Long, other numbers as Double. */
    static Object value(JsonElement element) {
        if (element == null || element.isJsonNull()) {
            return null;
        }
        if (!element.isJsonPrimitive()) {
            throw new ApiError(400, "BAD_REQUEST", "A tag value must be a string, number, boolean or null.");
        }
        JsonPrimitive primitive = element.getAsJsonPrimitive();
        if (primitive.isBoolean()) {
            return primitive.getAsBoolean();
        }
        if (primitive.isString()) {
            return primitive.getAsString();
        }
        BigDecimal number = primitive.getAsBigDecimal();
        if (!primitive.getAsString().matches(".*[.eE].*")) {
            try {
                return number.longValueExact();
            } catch (ArithmeticException error) {
                return number.doubleValue();
            }
        }
        return number.doubleValue();
    }

    // --- Gateway plumbing -----------------------------------------------------------------------------------

    private TagProvider provider(String name) {
        TagProvider provider = context.getTagManager().getTagProvider(name);
        if (provider == null) {
            throw new ApiError(404, "TAG_PROVIDER_NOT_FOUND", "No tag provider '" + name + "'.");
        }
        return provider;
    }

    /** Creates the missing folders from PDK down to the import base; existing ones are left as they are. */
    private static void ensureFolders(TagProvider provider, TagPath base) {
        List<TagPath> chain = new ArrayList<>();
        for (TagPath path = base; path != null && path.getPathLength() >= 1; path = path.getParentPath()) {
            chain.add(0, path);
        }
        List<TagConfiguration> folders = new ArrayList<>();
        for (TagPath path : chain) {
            TagConfiguration folder = BasicTagConfiguration.createNew(path);
            folder.setType(TagObjectType.Folder);
            folders.add(folder);
        }
        await(provider.saveTagConfigsAsync(folders, CollisionPolicy.Ignore, SecurityContext.systemContext()));
    }

    private static boolean browseInto(TagProvider provider, TagPath path, int levels, List<Map<String, Object>> out,
                                      boolean top) {
        Results<NodeDescription> results = await(
                provider.browseAsync(path, BrowseFilter.NONE, SecurityContext.systemContext()));
        QualityCode quality = results.getResultQuality();
        if (quality != null && !quality.isGood()) {
            if (top) {
                throw new ApiError(404, "TAG_NOT_FOUND", "Cannot browse '" + path.toStringFull() + "': "
                        + name(quality) + ".");
            }
            return false;
        }
        if (results.getResults() == null) {
            return false;
        }
        for (NodeDescription node : results.getResults()) {
            if (out.size() >= MAX_NODES) {
                return true;
            }
            Map<String, Object> entry = new LinkedHashMap<>();
            TagPath full = node.getFullPath();
            entry.put("path", full == null ? node.getName() : full.toStringFull());
            entry.put("type", node.getObjectType() == null ? null : node.getObjectType().name());
            if (node.getDataType() != null && node.getObjectType() != TagObjectType.Folder) {
                entry.put("dataType", node.getDataType().name());
                putValue(entry, node.getCurrentValue());
            }
            out.add(entry);
            if (node.hasChildren() && levels > 1 && full != null
                    && browseInto(provider, full, levels - 1, out, false)) {
                return true;
            }
        }
        return false;
    }

    private static void putValue(Map<String, Object> entry, QualifiedValue value) {
        if (value == null) {
            entry.put("value", null);
            entry.put("quality", null);
            return;
        }
        entry.put("value", plain(value.getValue()));
        entry.put("quality", name(value.getQuality()));
        Optional.ofNullable(value.getQuality()).map(QualityCode::getDiagnosticMessage)
                .filter(message -> !message.isBlank()).ifPresent(message -> entry.put("message", message));
        entry.put("timestamp", value.getTimestamp() == null ? null : value.getTimestamp().getTime());
    }

    private static Object plain(Object value) {
        if (value == null || value instanceof Number || value instanceof Boolean || value instanceof String) {
            return value;
        }
        if (value instanceof Date date) {
            return date.getTime();
        }
        return String.valueOf(value);
    }

    private static String name(QualityCode quality) {
        return quality == null ? null : quality.getName();
    }

    /** Import results: how many of each quality, Good first. */
    private static Map<String, Integer> summarize(List<QualityCode> qualities) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (QualityCode quality : qualities) {
            counts.merge(String.valueOf(name(quality)), 1, Integer::sum);
        }
        return counts;
    }

    private static JsonObject object(String body) {
        try {
            JsonElement parsed = JsonParser.parseString(body == null ? "" : body);
            if (!parsed.isJsonObject()) {
                throw new ApiError(400, "BAD_REQUEST", "The body must be a JSON object.");
            }
            return parsed.getAsJsonObject();
        } catch (RuntimeException error) {
            if (error instanceof ApiError apiError) {
                throw apiError;
            }
            throw new ApiError(400, "BAD_REQUEST", "The body is not valid JSON: " + error.getMessage());
        }
    }

    private static List<String> strings(JsonObject json, String key) {
        JsonElement element = json.get(key);
        if (element == null || !element.isJsonArray() || element.getAsJsonArray().size() == 0
                || element.getAsJsonArray().size() > MAX_ITEMS) {
            throw new ApiError(400, "BAD_REQUEST", "'" + key + "' must be an array of 1 to " + MAX_ITEMS + " strings.");
        }
        List<String> result = new ArrayList<>();
        JsonArray array = element.getAsJsonArray();
        for (JsonElement item : array) {
            result.add(string(item, key));
        }
        return result;
    }

    private static String string(JsonElement element, String key) {
        if (element == null || !element.isJsonPrimitive() || !element.getAsJsonPrimitive().isString()) {
            throw new ApiError(400, "BAD_REQUEST", "'" + key + "' must be a string.");
        }
        return element.getAsString();
    }

    private static <T> T await(CompletableFuture<T> future) {
        try {
            return future.get(TIMEOUT_MS, TimeUnit.MILLISECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new ApiError(503, "INTERRUPTED", "Interrupted while waiting for the tag system.");
        } catch (TimeoutException error) {
            throw new ApiError(504, "TAGS_BUSY", "The tag system did not answer within " + TIMEOUT_MS + " ms.");
        } catch (ExecutionException error) {
            throw new ApiError(500, "TAG_OPERATION_FAILED", String.valueOf(error.getCause()));
        }
    }
}
