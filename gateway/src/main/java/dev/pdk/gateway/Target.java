package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.resourcecollection.ResourcePath;
import com.inductiveautomation.ignition.common.resourcecollection.ResourceType;

import java.util.regex.Pattern;

/** A resource address taken from the URL tail "<moduleId>/<typeId>[/<path>]". */
record Target(ResourceType type, String path) {
    private static final Pattern ID = Pattern.compile("[A-Za-z0-9._-]+");
    // Resource names become directories on disk: reject what Windows or a path traversal would misread.
    private static final Pattern FORBIDDEN = Pattern.compile("[\\\\:*?\"<>|\\p{Cntrl}]");

    static Target parse(String tail) {
        String value = tail == null ? "" : tail;
        while (value.endsWith("/")) {
            value = value.substring(0, value.length() - 1);
        }
        String[] parts = value.split("/", 3);
        if (parts.length < 2 || !ID.matcher(parts[0]).matches() || !ID.matcher(parts[1]).matches()) {
            throw new ApiError(400, "INVALID_PATH", "Expected <moduleId>/<typeId>[/<path>], got '" + tail + "'.");
        }
        String path = parts.length == 3 ? parts[2] : "";
        if (!path.isEmpty()) {
            for (String segment : path.split("/", -1)) {
                if (segment.isEmpty() || segment.equals(".") || segment.equals("..")
                        || !segment.equals(segment.strip()) || FORBIDDEN.matcher(segment).find()) {
                    throw new ApiError(400, "INVALID_PATH", "Invalid resource name '" + segment + "' in '" + path + "'.");
                }
            }
        }
        return new Target(new ResourceType(parts[0], parts[1]), path);
    }

    ResourcePath resourcePath() {
        return new ResourcePath(type, path);
    }

    String typeKey() {
        return type.moduleId() + "/" + type.typeId();
    }

    String display() {
        return path.isEmpty() ? typeKey() : typeKey() + "/" + path;
    }

    /** True for resources strictly below this target. */
    boolean contains(ResourcePath candidate) {
        String candidatePath = candidate.getPath().toString();
        return path.isEmpty() ? !candidatePath.isEmpty() : candidatePath.startsWith(path + "/");
    }
}
