package dev.pdk.gateway;

import java.util.Optional;

/**
 * The projects PDK may write to. -Dpdk.sandbox=<project> takes any resource; without it the module is read-only.
 * -Dpdk.target=<project> (optional, never the sandbox) takes only finished work: Perspective views, style classes
 * and project scripts outside PDK/, and the project stylesheet.
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

    void requireEnabled() {
        if (name == null) {
            throw new ApiError(403, "WRITE_DISABLED",
                    "Writes are disabled: the gateway was not started with -D" + PROPERTY + "=<project>.");
        }
    }

    void requireWritable(String project) {
        requireEnabled();
        if (!name.equals(project)) {
            throw new ApiError(403, "OUTSIDE_SANDBOX",
                    "Project '" + project + "' is not the sandbox '" + name + "'.");
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
