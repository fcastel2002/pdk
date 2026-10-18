package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.ImmutableBytes;
import com.inductiveautomation.ignition.common.gson.JsonElement;
import com.inductiveautomation.ignition.common.gson.JsonObject;
import com.inductiveautomation.ignition.common.gson.JsonParser;
import com.inductiveautomation.ignition.common.model.ApplicationScope;
import com.inductiveautomation.ignition.common.resourcecollection.ChangeOperation;
import com.inductiveautomation.ignition.common.resourcecollection.LastModification;
import com.inductiveautomation.ignition.common.resourcecollection.LockAcquisitionTimeoutException;
import com.inductiveautomation.ignition.common.resourcecollection.PushException;
import com.inductiveautomation.ignition.common.resourcecollection.Resource;
import com.inductiveautomation.ignition.common.resourcecollection.ResourceBuilder;
import com.inductiveautomation.ignition.common.resourcecollection.ResourcePath;
import com.inductiveautomation.ignition.common.resourcecollection.ResourceSignature;
import com.inductiveautomation.ignition.common.resourcecollection.RuntimeResourceCollection;
import com.inductiveautomation.ignition.gateway.resourcecollection.PushOperation;
import com.inductiveautomation.ignition.gateway.resourcecollection.ResourceCollectionManager;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryNotEmptyException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.regex.Pattern;

/**
 * Project resources through ResourceCollectionManager.push() with the expected signature.
 * A write based on a stale signature is a 409 carrying the current state; it never overwrites.
 */
final class Resources {
    static final String ACTOR = "pdk";
    /** Context object of every PDK push: listeners use it to tell PDK writes from Designer saves. */
    static final Object ORIGIN = new Object() {
        @Override
        public String toString() {
            return "pdk";
        }
    };
    private static final long LOCK_TIMEOUT_MS = 10_000;
    private static final long NOTIFY_TIMEOUT_MS = 15_000;
    private static final long SCAN_TIMEOUT_MS = 60_000;
    private static final Pattern FILE_NAME = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]*");
    private static final Pattern HEX = Pattern.compile("(?:[0-9a-fA-F]{2})+");
    static final String PAGE_FILE = "config.json";
    private static final Pattern SCOPE = Pattern.compile("[GCD]{1,3}|A|N");
    private static final Set<String> TEXT_SUFFIXES =
            Set.of(".json", ".py", ".txt", ".css", ".js", ".md", ".xml", ".svg", ".html");

    private final ResourceCollectionManager manager;
    private final Sandbox sandbox;
    private final Path projectsDir;

    Resources(ResourceCollectionManager manager, Sandbox sandbox, Path projectsDir) {
        this.manager = manager;
        this.sandbox = sandbox;
        this.projectsDir = projectsDir;
    }

    Reply read(String project, Target target) {
        RuntimeResourceCollection collection = collection(project);
        Optional<Resource> resource = collection.getResource(target.resourcePath());
        if (resource.isPresent() && !resource.get().isFolder()) {
            return Reply.ok(describe(project, target, resource.get(), true));
        }
        List<Map<String, Object>> children = collection.getResourcesOfType(target.type()).stream()
                .filter(candidate -> target.contains(candidate.getResourcePath()))
                .sorted(Comparator.comparing(Resources::pathOf))
                .map(Resources::summary)
                .toList();
        if (resource.isEmpty() && children.isEmpty() && !target.path().isEmpty()) {
            throw new ApiError(404, "NOT_FOUND", target.display() + " does not exist in " + project + ".");
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("project", project);
        body.put("type", target.typeKey());
        body.put("path", target.path());
        body.put("folder", true);
        body.put("signature", resource.map(Resources::signatureOf).orElse(null));
        body.put("children", children);
        return Reply.ok(body);
    }

    Reply write(String project, Target target, String body) {
        boolean targetPages = sandbox.isTargetPages(project, target);
        if (targetPages) {
            sandbox.requireEnabled();
        } else {
            sandbox.requireWritable(project, target);
        }
        requireMutable(project);
        WriteRequest request = WriteRequest.parse(body);
        Optional<Resource> current = manager.getResource(project, target.resourcePath());
        if (targetPages) {
            requirePreviewsOnly(project, current, request);
        }
        ChangeOperation change;
        String op;
        if (request.signature() == null) {
            if (current.isPresent()) {
                throw conflict(project, target, current, "it already exists");
            }
            change = ChangeOperation.newCreateOp(LastModification.update(newResource(project, target, request), ACTOR));
            op = "created";
        } else {
            Resource existing = current.orElseThrow(() -> conflict(project, target, current, "it no longer exists"));
            if (existing.isFolder()) {
                throw new ApiError(400, "IS_FOLDER", target.display() + " is a folder.");
            }
            if (!signatureOf(existing).equals(request.signature())) {
                throw conflict(project, target, current, "it changed after signature " + request.signature());
            }
            ResourceBuilder builder = existing.toBuilder();
            request.files().forEach((name, text) -> builder.putData(name, text.getBytes(StandardCharsets.UTF_8)));
            change = ChangeOperation.newModifyOp(LastModification.update(builder.build(), ACTOR),
                    expected(existing, request.signature()));
            op = "modified";
        }
        boolean notified = push(project, target, change, request.signature());
        Resource written = manager.getResource(project, target.resourcePath())
                .orElseThrow(() -> new ApiError(500, "WRITE_NOT_VISIBLE", target.display() + " was pushed but is not visible."));
        GatewayHook.LOG.info("PDK {} {}:{} {} -> {}", op, project, target.display(), request.signature(), signatureOf(written));
        Map<String, Object> result = describe(project, target, written, false);
        result.put("op", op);
        result.put("notified", notified);
        return new Reply("created".equals(op) ? 201 : 200, result);
    }

    /** The target's page configuration changes only in its /pdk/ preview pages ({@link Sandbox#onlyPreviews}). */
    private static void requirePreviewsOnly(String project, Optional<Resource> current, WriteRequest request) {
        String before = current.flatMap(resource -> resource.getData(PAGE_FILE))
                .flatMap(bytes -> decode(bytes.getBytes())).orElse("");
        if (!request.files().keySet().equals(Set.of(PAGE_FILE))
                || !Sandbox.onlyPreviews(before, request.files().get(PAGE_FILE))) {
            throw new ApiError(403, "OUTSIDE_TARGET", "The target '" + project + "' takes only preview pages under "
                    + Sandbox.PREVIEW_PREFIX + " in its page configuration, each one a view; its own pages, docks and "
                    + "settings stay as they are.");
        }
    }

    Reply delete(String project, Target target, String signature) {
        sandbox.requireWritable(project, target);
        requireMutable(project);
        if (signature == null || signature.isBlank()) {
            throw new ApiError(400, "SIGNATURE_REQUIRED", "Pass ?signature=<current signature> to delete.");
        }
        String expectedSignature = normalizeSignature(signature);
        Optional<Resource> current = manager.getResource(project, target.resourcePath());
        Resource existing = current
                .orElseThrow(() -> new ApiError(404, "NOT_FOUND", target.display() + " does not exist in " + project + "."));
        if (!signatureOf(existing).equals(expectedSignature)) {
            throw conflict(project, target, current, "it changed after signature " + expectedSignature);
        }
        if (existing.isFolder() && hasChildren(project, target.resourcePath())) {
            throw new ApiError(409, "FOLDER_NOT_EMPTY", target.display() + " still has resources.");
        }
        push(project, target, ChangeOperation.newDeleteOp(expected(existing, expectedSignature)), expectedSignature);
        if (existing.isFolder()) {
            removeEmptyDirectory(project, target, target.path());
        }
        List<String> pruned = pruneEmptyFolders(project, target);
        GatewayHook.LOG.info("PDK deleted {}:{} {}, pruned {}", project, target.display(), expectedSignature, pruned);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("project", project);
        result.put("type", target.typeKey());
        result.put("path", target.path());
        result.put("op", "deleted");
        result.put("pruned", pruned);
        return Reply.ok(result);
    }

    /**
     * The gateway creates the folders of a new resource implicitly but keeps them when the resource goes.
     * Delete the ancestors this delete left empty, deepest first, and any directory the storage left behind.
     */
    private List<String> pruneEmptyFolders(String project, Target target) {
        List<String> pruned = new ArrayList<>();
        for (String path : ancestors(target.path())) {
            ResourcePath folder = new ResourcePath(target.type(), path);
            Optional<Resource> resource = manager.getResource(project, folder);
            if (resource.isEmpty() || !resource.get().isFolder() || hasChildren(project, folder)) {
                break;
            }
            CompletableFuture<Void> listeners;
            try {
                listeners = manager.push(new PushOperation(
                        List.of(ChangeOperation.newDeleteOp(resource.get().getResourceSignature())), ORIGIN, LOCK_TIMEOUT_MS));
            } catch (LockAcquisitionTimeoutException error) {
                GatewayHook.LOG.warn("PDK could not prune folder {}:{}/{}: project locked", project, target.typeKey(), path);
                break;
            } catch (PushException error) {
                GatewayHook.LOG.warn("PDK could not prune folder {}:{}/{}", project, target.typeKey(), path, error);
                break;
            }
            await(listeners, NOTIFY_TIMEOUT_MS);
            removeEmptyDirectory(project, target, path);
            pruned.add(path);
        }
        return pruned;
    }

    private void removeEmptyDirectory(String project, Target target, String path) {
        Path directory = projectsDir.resolve(project).resolve(target.type().moduleId()).resolve(target.type().typeId())
                .resolve(path).normalize();
        if (!directory.startsWith(projectsDir.resolve(project)) || !Files.isDirectory(directory)) {
            return;
        }
        try {
            Files.delete(directory);
            GatewayHook.LOG.info("PDK removed the empty directory {} the storage left behind", directory);
        } catch (DirectoryNotEmptyException error) {
            GatewayHook.LOG.warn("PDK deleted folder {} but its directory is not empty", directory);
        } catch (IOException error) {
            GatewayHook.LOG.warn("PDK could not remove the directory {}", directory, error);
        }
    }

    /** "A/B/C" -> ["A/B", "A"]: the folders above a resource, deepest first. */
    static List<String> ancestors(String path) {
        List<String> result = new ArrayList<>();
        for (int slash = path.lastIndexOf('/'); slash > 0; slash = path.lastIndexOf('/', slash - 1)) {
            result.add(path.substring(0, slash));
        }
        return result;
    }

    /** Direct children by browse(), and, in case browse() misses any, resources anywhere below the folder. */
    private boolean hasChildren(String project, ResourcePath folder) {
        RuntimeResourceCollection collection = collection(project);
        String prefix = folder.getPath().toString() + "/";
        return collection.browse(folder).map(children -> !children.isEmpty()).orElse(false)
                || collection.getResourcesOfType(folder.getResourceType()).stream()
                        .anyMatch(resource -> pathOf(resource).startsWith(prefix));
    }

    /** Pick up files changed on disk behind the gateway's back (an editor, git); the Designer does not need it. */
    Reply scan() {
        sandbox.requireEnabled();
        boolean finished = await(manager.requestScan(), SCAN_TIMEOUT_MS);
        GatewayHook.LOG.info("PDK requested a project scan (finished: {})", finished);
        return Reply.ok(Map.of("scanned", finished));
    }

    /** Push and wait for the listeners, so the runtime already serves the change when we answer. */
    private boolean push(String project, Target target, ChangeOperation change, String expectedSignature) {
        CompletableFuture<Void> listeners;
        try {
            listeners = manager.push(new PushOperation(List.of(change), ORIGIN, LOCK_TIMEOUT_MS));
        } catch (LockAcquisitionTimeoutException error) {
            throw new ApiError(503, "BUSY", "Another push holds the project lock; retry.");
        } catch (PushException error) {
            Optional<Resource> current = manager.getResource(project, target.resourcePath());
            if (isConflict(change, current, expectedSignature)) {
                throw conflict(project, target, current, error.getMessage());
            }
            throw new ApiError(422, "PUSH_REJECTED", String.valueOf(error.getMessage()));
        }
        return await(listeners, NOTIFY_TIMEOUT_MS);
    }

    /** Wait for the listeners of an applied push; false if they did not finish cleanly in time. */
    private static boolean await(CompletableFuture<Void> listeners, long timeoutMs) {
        try {
            listeners.get(timeoutMs, TimeUnit.MILLISECONDS);
            return true;
        } catch (TimeoutException error) {
            return false;
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            return false;
        } catch (ExecutionException error) {
            GatewayHook.LOG.warn("PDK push applied but a listener failed", error.getCause());
            return false;
        }
    }

    private static boolean isConflict(ChangeOperation change, Optional<Resource> current, String expectedSignature) {
        return switch (change.getOperationType()) {
            case CREATE -> current.isPresent();
            case MODIFY, DELETE -> current.isEmpty() || !signatureOf(current.get()).equals(expectedSignature);
            default -> false;
        };
    }

    private ApiError conflict(String project, Target target, Optional<Resource> current, String reason) {
        Map<String, Object> extra = new LinkedHashMap<>();
        extra.put("current", current.map(resource -> describe(project, target, resource, true)).orElse(null));
        return new ApiError(409, "CONFLICT", target.display() + " was not written: " + reason + ".", extra);
    }

    private RuntimeResourceCollection collection(String project) {
        return manager.find(project)
                .orElseThrow(() -> new ApiError(404, "PROJECT_NOT_FOUND", "Project '" + project + "' does not exist."));
    }

    private void requireMutable(String project) {
        collection(project);
        if (!manager.isMutable(project)) {
            throw new ApiError(409, "IMMUTABLE", "Project '" + project + "' is immutable.");
        }
    }

    private static Resource newResource(String project, Target target, WriteRequest request) {
        ResourceBuilder builder = Resource.newBuilder()
                .setResourceCollectionName(project)
                .setResourcePath(target.resourcePath())
                .setApplicationScope(request.scope())
                .setVersion(1)
                .setRestricted(false)
                .setOverridable(true);
        request.files().forEach((name, text) -> builder.putData(name, text.getBytes(StandardCharsets.UTF_8)));
        return builder.build();
    }

    private static ResourceSignature expected(Resource existing, String signature) {
        return new ResourceSignature(existing.getResourceId(), ImmutableBytes.fromHexString(signature));
    }

    static String signatureOf(Resource resource) {
        return resource.getResourceSignature().signature().getAsHexString().toLowerCase(Locale.ROOT);
    }

    static String normalizeSignature(String value) {
        String trimmed = value.strip();
        if (!HEX.matcher(trimmed).matches()) {
            throw new ApiError(400, "BAD_SIGNATURE", "A signature is a hex string, got '" + value + "'.");
        }
        return trimmed.toLowerCase(Locale.ROOT);
    }

    private static String pathOf(Resource resource) {
        return resource.getResourcePath().getPath().toString();
    }

    private static Map<String, Object> summary(Resource resource) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("path", pathOf(resource));
        result.put("folder", resource.isFolder());
        result.put("signature", signatureOf(resource));
        result.put("lastModification", lastModification(resource));
        return result;
    }

    private static Map<String, Object> describe(String project, Target target, Resource resource, boolean withFiles) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("project", project);
        result.put("type", target.typeKey());
        result.put("path", pathOf(resource));
        result.put("signature", signatureOf(resource));
        result.put("folder", resource.isFolder());
        result.put("scope", ApplicationScope.toCode(resource.getApplicationScope()));
        result.put("definedIn", resource.getDefiningCollectionName());
        result.put("lastModification", lastModification(resource));
        if (withFiles) {
            Map<String, Object> files = new LinkedHashMap<>();
            Map<String, Object> binary = new LinkedHashMap<>();
            for (String key : resource.getDataKeys().stream().sorted().toList()) {
                byte[] bytes = resource.getData(key).map(ImmutableBytes::getBytes).orElse(new byte[0]);
                Optional<String> text = TEXT_SUFFIXES.stream().anyMatch(key::endsWith) ? decode(bytes) : Optional.empty();
                if (text.isPresent()) {
                    files.put(key, text.get());
                } else {
                    binary.put(key, bytes.length);
                }
            }
            result.put("files", files);
            result.put("binaryFiles", binary);
        }
        return result;
    }

    private static Map<String, Object> lastModification(Resource resource) {
        return LastModification.of(resource).map(modification -> {
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("actor", modification.actor());
            result.put("timestamp", modification.timestamp() == null ? null : modification.timestamp().toInstant().toString());
            return result;
        }).orElse(null);
    }

    private static Optional<String> decode(byte[] bytes) {
        try {
            return Optional.of(StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes))
                    .toString());
        } catch (CharacterCodingException error) {
            return Optional.empty();
        }
    }

    /** PUT body: {"files": {"view.json": "<text>"}, "signature": "<hex>" | null, "scope": "G"}. */
    record WriteRequest(Map<String, String> files, String signature, String scope) {
        static WriteRequest parse(String body) {
            JsonObject json;
            try {
                JsonElement parsed = JsonParser.parseString(body == null ? "" : body);
                if (!parsed.isJsonObject()) {
                    throw new ApiError(400, "BAD_REQUEST", "The body must be a JSON object.");
                }
                json = parsed.getAsJsonObject();
            } catch (RuntimeException error) {
                if (error instanceof ApiError apiError) {
                    throw apiError;
                }
                throw new ApiError(400, "BAD_REQUEST", "The body is not valid JSON: " + error.getMessage());
            }
            JsonElement filesElement = json.get("files");
            if (filesElement == null || !filesElement.isJsonObject() || filesElement.getAsJsonObject().size() == 0) {
                throw new ApiError(400, "BAD_REQUEST", "'files' must be a non-empty object of file name -> text.");
            }
            Map<String, String> files = new LinkedHashMap<>();
            for (Map.Entry<String, JsonElement> entry : filesElement.getAsJsonObject().entrySet()) {
                String name = entry.getKey();
                JsonElement value = entry.getValue();
                if (!FILE_NAME.matcher(name).matches() || name.equals("resource.json")) {
                    throw new ApiError(400, "INVALID_FILE_NAME", "Invalid resource file name '" + name + "'.");
                }
                if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) {
                    throw new ApiError(400, "BAD_REQUEST", "File '" + name + "' must be a JSON string.");
                }
                files.put(name, value.getAsString());
            }
            String signature = optionalString(json, "signature");
            String scope = optionalString(json, "scope");
            return new WriteRequest(files, signature == null ? null : normalizeSignature(signature),
                    scope == null ? "G" : checkScope(scope));
        }

        /** G, C, D (each at most once, any order), A for all or N for none; anything else is a 400, not a 500. */
        static String checkScope(String scope) {
            String code = scope.strip().toUpperCase(Locale.ROOT);
            boolean valid = SCOPE.matcher(code).matches() && code.chars().distinct().count() == code.length();
            if (!valid) {
                throw new ApiError(400, "BAD_REQUEST",
                        "'scope' is a combination of G, C and D (or A, N), got '" + scope + "'.");
            }
            return code;
        }

        private static String optionalString(JsonObject json, String key) {
            JsonElement value = json.get(key);
            if (value == null || value.isJsonNull()) {
                return null;
            }
            if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) {
                throw new ApiError(400, "BAD_REQUEST", "'" + key + "' must be a string or null.");
            }
            return value.getAsString();
        }
    }
}
