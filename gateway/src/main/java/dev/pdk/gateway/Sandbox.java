package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.gson.JsonElement;
import com.inductiveautomation.ignition.common.gson.JsonObject;
import com.inductiveautomation.ignition.common.gson.JsonParser;

import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * The projects PDK may write to. -Dpdk.sandbox=<project> takes any resource; without it the module is read-only.
 * -Dpdk.target=<project> (optional, never the sandbox) takes only finished work: Perspective views, style classes
 * and project scripts outside PDK/, the project stylesheet, and preview pages under /pdk/ in its page configuration
 * (so the eyes can open a promoted view there).
 */
final class Sandbox {
    static final String PROPERTY = "pdk.sandbox";
    static final String TARGET_PROPERTY = "pdk.target";
    static final String VIEWS = "com.inductiveautomation.perspective/views";
    static final String STYLESHEET = "com.inductiveautomation.perspective/stylesheet";
    static final String STYLE_CLASSES = "com.inductiveautomation.perspective/style-classes";
    static final String SCRIPTS = "ignition/script-python";
    /** What the target takes outside PDK/, through promote. */
    static final java.util.Set<String> FINISHED = java.util.Set.of(VIEWS, STYLE_CLASSES, SCRIPTS);
    static final String WORK_FOLDER = "PDK";
    static final String PAGE_CONFIG = "com.inductiveautomation.perspective/page-config";
    /** The eyes' preview pages: /pdk/<view path>. */
    static final String PREVIEW_PREFIX = "/pdk/";

    private final String name;
    private final String target;

    private Sandbox(String name, String target) {
        this.name = name;
        this.target = target;
    }

    static Sandbox fromSystemProperties() {
        return of(System.getProperty(PROPERTY), System.getProperty(TARGET_PROPERTY));
    }

    static Sandbox of(String value) {
        return of(value, null);
    }

    static Sandbox of(String value, String targetValue) {
        String sandbox = blankToNull(value);
        String target = blankToNull(targetValue);
        // A target equal to the sandbox, or without one, would only blur the roles: ignore it (Health reports it).
        return new Sandbox(sandbox, sandbox == null || sandbox.equals(target) ? null : target);
    }

    private static String blankToNull(String value) {
        String trimmed = value == null ? "" : value.strip();
        return trimmed.isEmpty() ? null : trimmed;
    }

    Optional<String> name() {
        return Optional.ofNullable(name);
    }

    Optional<String> target() {
        return Optional.ofNullable(target);
    }

    /** The configured -Dpdk.target, ignored or not, for Health. */
    static Optional<String> requestedTarget() {
        return Optional.ofNullable(blankToNull(System.getProperty(TARGET_PROPERTY)));
    }

    boolean writeEnabled() {
        return name != null;
    }

    /** The target's page configuration: it takes preview pages only ({@link #onlyPreviews}), checked on write. */
    boolean isTargetPages(String project, Target resource) {
        return target != null && target.equals(project) && PAGE_CONFIG.equals(resource.typeKey())
                && resource.path().isEmpty();
    }

    /**
     * True when a new page configuration differs from the current one only in pages under /pdk/, each one a view:
     * the eyes' preview pages of promoted views. The project's own pages, docks and settings stay as they are.
     */
    static boolean onlyPreviews(String before, String after) {
        JsonObject old;
        JsonObject updated;
        try {
            old = before == null || before.isBlank() ? new JsonObject() : JsonParser.parseString(before)
                    .getAsJsonObject();
            updated = JsonParser.parseString(after == null ? "" : after).getAsJsonObject();
        } catch (RuntimeException error) {
            return false;
        }
        JsonObject newPages = pages(updated);
        for (Map.Entry<String, JsonElement> page : newPages.entrySet()) {
            if (page.getKey().startsWith(PREVIEW_PREFIX) && !isViewPage(page.getValue())) {
                return false;
            }
        }
        return withoutPreviews(old).equals(withoutPreviews(updated));
    }

    private static boolean isViewPage(JsonElement page) {
        if (!page.isJsonObject()) {
            return false;
        }
        JsonObject value = page.getAsJsonObject();
        JsonElement view = value.get("viewPath");
        return value.size() <= 2 && view != null && view.isJsonPrimitive() && !view.getAsString().isBlank()
                && (value.size() == 1 || value.has("title"));
    }

    private static JsonObject pages(JsonObject config) {
        JsonElement pages = config.get("pages");
        return pages != null && pages.isJsonObject() ? pages.getAsJsonObject() : new JsonObject();
    }

    private static JsonObject withoutPreviews(JsonObject config) {
        JsonObject copy = config.deepCopy();
        JsonObject pages = pages(copy);
        for (String key : List.copyOf(pages.keySet())) {
            if (key.startsWith(PREVIEW_PREFIX)) {
                pages.remove(key);
            }
        }
        return copy;
    }

    void requireEnabled() {
        if (name == null) {
            throw new ApiError(403, "WRITE_DISABLED",
                    "Writes are disabled: the gateway was not started with -D" + PROPERTY + "=<project>.");
        }
    }

    /**
     * Closing a session PDK's eyes opened: in the sandbox, or in the target, where they look at promoted views.
     * It changes no resource; Live.close only closes the session that holds the given page.
     */
    void requireSessionProject(String project) {
        requireEnabled();
        if (!name.equals(project) && (target == null || !target.equals(project))) {
            throw new ApiError(403, "OUTSIDE_SANDBOX",
                    "Project '" + project + "' is not the sandbox '" + name + "'"
                            + (target == null ? "." : " nor the target '" + target + "'."));
        }
    }

    /** Writes and deletes of a resource: anything in the sandbox; finished work and the stylesheet in the target. */
    void requireWritable(String project, Target resource) {
        requireEnabled();
        if (name.equals(project)) {
            return;
        }
        if (target == null || !target.equals(project)) {
            throw new ApiError(403, "OUTSIDE_SANDBOX",
                    "Project '" + project + "' is not the sandbox '" + name + "'"
                            + (target == null ? "." : " nor the target '" + target + "'."));
        }
        String path = resource.path();
        if (STYLESHEET.equals(resource.typeKey()) && path.isEmpty()) {
            return;
        }
        if (!FINISHED.contains(resource.typeKey()) || path.isEmpty()
                || path.equals(WORK_FOLDER) || path.startsWith(WORK_FOLDER + "/")) {
            throw new ApiError(403, "OUTSIDE_TARGET",
                    "The target '" + target + "' only takes views, style classes and scripts outside " + WORK_FOLDER
                            + "/ and the stylesheet, not " + resource.display() + ".");
        }
    }
}
