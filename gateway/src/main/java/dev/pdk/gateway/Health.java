package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.BundleUtil;
import com.inductiveautomation.ignition.gateway.model.GatewayContext;
import com.inductiveautomation.ignition.gateway.model.GatewayModule;

import java.util.LinkedHashMap;
import java.util.Map;

/** Versions and write state, so the client can check it talks to the gateway it expects. */
final class Health {
    static final int API_VERSION = 1;

    private final GatewayContext context;
    private final Sandbox sandbox;
    private final Live live;
    private final LogTap logs;
    private final Tags tags;

    Health(GatewayContext context, Sandbox sandbox, Live live, LogTap logs, Tags tags) {
        this.context = context;
        this.sandbox = sandbox;
        this.live = live;
        this.logs = logs;
        this.tags = tags;
    }

    Map<String, Object> describe() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("api", API_VERSION);
        result.put("module", GatewayHook.MODULE_ID);
        result.put("moduleVersion", moduleVersion(GatewayHook.MODULE_ID));
        result.put("ignitionVersion", platformVersion());
        result.put("perspectiveVersion", moduleVersion("com.inductiveautomation.perspective"));
        result.put("edition", System.getProperty("edition"));
        result.put("java", System.getProperty("java.runtime.version"));
        result.put("sandbox", sandbox.name().orElse(null));
        result.put("writeEnabled", sandbox.writeEnabled());
        result.put("sandboxExists", sandbox.name()
                .map(name -> context.getProjectManager().find(name).isPresent())
                .orElse(false));
        result.put("target", sandbox.target().orElse(null));
        result.put("targetExists", sandbox.target()
                .map(name -> context.getProjectManager().find(name).isPresent())
                .orElse(false));
        // Set but ignored: equal to the sandbox, or without a sandbox.
        result.put("targetIgnored", Sandbox.requestedTarget().filter(name -> sandbox.target().isEmpty()).orElse(null));
        result.put("runtime", live.available());
        result.put("logs", logs.isAttached());
        result.put("tags", tags.describe());
        return result;
    }

    private String moduleVersion(String id) {
        GatewayModule module = context.getModuleManager().getModule(id);
        return module == null ? null : String.valueOf(module.getInfo().getVersion());
    }

    private static String platformVersion() {
        try {
            return String.valueOf(BundleUtil.get().getPlatformVersion());
        } catch (RuntimeException | LinkageError error) {
            return null;
        }
    }
}
