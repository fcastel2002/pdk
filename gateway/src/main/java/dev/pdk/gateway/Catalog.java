package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.gson.JsonElement;
import com.inductiveautomation.ignition.common.jsonschema.BaseJsonValidator;
import com.inductiveautomation.ignition.common.jsonschema.JsonSchema;
import com.inductiveautomation.ignition.gateway.model.GatewayContext;
import com.inductiveautomation.perspective.common.api.ComponentDescriptor;
import com.inductiveautomation.perspective.common.api.ComponentEventDescriptor;
import com.inductiveautomation.perspective.common.api.ExtensionFunctionDescriptor;
import com.inductiveautomation.perspective.common.api.PaletteEntry;
import com.inductiveautomation.perspective.gateway.api.PerspectiveContext;

import java.lang.reflect.Method;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The components registered in this gateway's Perspective, third-party modules included, in the shape of
 * ia.components.json. The raw JSON of a JsonSchema is not public API: it is read by reflection, and if that
 * stops linking the schemas come back null with schemaAvailable=false instead of failing.
 */
final class Catalog {
    private static final Method SCHEMA_NODE = schemaNodeAccessor();

    private final GatewayContext context;

    Catalog(GatewayContext context) {
        this.context = context;
    }

    Reply list(boolean full) {
        List<Map<String, Object>> components = descriptors().values().stream()
                .sorted(Comparator.comparing(ComponentDescriptor::id))
                .map(descriptor -> full ? describe(descriptor) : summary(descriptor))
                .toList();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schemaAvailable", SCHEMA_NODE != null);
        result.put("count", components.size());
        result.put("components", components);
        return Reply.ok(result);
    }

    Reply one(String id) {
        ComponentDescriptor descriptor = descriptors().get(id);
        if (descriptor == null) {
            throw new ApiError(404, "NOT_FOUND", "No component '" + id + "' is registered.");
        }
        return Reply.ok(describe(descriptor));
    }

    private Map<String, ComponentDescriptor> descriptors() {
        try {
            return PerspectiveContext.get(context).getComponentRegistry().get();
        } catch (RuntimeException | LinkageError error) {
            throw new ApiError(503, "PERSPECTIVE_UNAVAILABLE", "Perspective's component registry is not available: "
                    + error.getClass().getSimpleName());
        }
    }

    private static Map<String, Object> summary(ComponentDescriptor descriptor) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("id", descriptor.id());
        result.put("name", descriptor.name());
        result.put("category", descriptor.paletteCategory());
        result.put("moduleId", descriptor.moduleId());
        result.put("deprecated", descriptor.deprecated());
        result.put("container", descriptor.childPositionDefaults().isPresent());
        return result;
    }

    private static Map<String, Object> describe(ComponentDescriptor descriptor) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("id", descriptor.id());
        result.put("name", descriptor.name());
        result.put("moduleId", descriptor.moduleId());
        result.put("deprecated", descriptor.deprecated());
        result.put("defaultMetaName", descriptor.defaultMetaName());
        Map<String, Object> palette = new LinkedHashMap<>();
        palette.put("category", descriptor.paletteCategory());
        palette.put("variants", descriptor.paletteEntries().stream().map(Catalog::variant).toList());
        result.put("palette", palette);
        result.put("schema", raw(descriptor.schema()));
        result.put("childPositionSchema", raw(descriptor.childPositionSchema()));
        result.put("events", descriptor.events().stream().map(Catalog::event).toList());
        result.put("extensionFunctions", descriptor.extensionFunctions().stream().map(Catalog::function).toList());
        return result;
    }

    private static Map<String, Object> variant(PaletteEntry entry) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("id", entry.getVariantId());
        result.put("label", entry.getLabel());
        result.put("tooltip", entry.getTooltip());
        result.put("props", entry.getInitialProps());
        return result;
    }

    private static Map<String, Object> event(ComponentEventDescriptor event) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("name", event.getName());
        result.put("description", event.getDescription());
        result.put("documentationUrl", event.getDocumentationUrl());
        result.put("schema", raw(event.getSchema()));
        return result;
    }

    private static Map<String, Object> function(ExtensionFunctionDescriptor function) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("name", function.getName());
        result.put("description", function.getDescription());
        result.put("defaultImplementation", function.getDefaultImplementation());
        result.put("arguments", function.getArguments().stream().map(argument -> {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("name", argument.getName());
            entry.put("description", argument.getDescription());
            return entry;
        }).toList());
        return result;
    }

    static JsonElement raw(JsonSchema schema) {
        if (schema == null || SCHEMA_NODE == null) {
            return null;
        }
        try {
            return (JsonElement) SCHEMA_NODE.invoke(schema);
        } catch (ReflectiveOperationException | RuntimeException error) {
            return null;
        }
    }

    private static Method schemaNodeAccessor() {
        try {
            Method method = BaseJsonValidator.class.getDeclaredMethod("getSchemaNode");
            method.setAccessible(true);
            return method;
        } catch (ReflectiveOperationException | RuntimeException error) {
            GatewayHook.LOG.warn("PDK cannot read raw component schemas; /catalog returns them as null", error);
            return null;
        }
    }
}
