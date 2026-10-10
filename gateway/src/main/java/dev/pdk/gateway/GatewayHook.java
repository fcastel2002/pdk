package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.licensing.LicenseState;
import com.inductiveautomation.ignition.gateway.dataroutes.RouteGroup;
import com.inductiveautomation.ignition.gateway.model.AbstractGatewayModuleHook;
import com.inductiveautomation.ignition.gateway.model.GatewayContext;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Optional;

/** PDK gateway module: a loopback-only, token-protected resource API for development gateways. */
public final class GatewayHook extends AbstractGatewayModuleHook {
    public static final String MODULE_ID = "dev.pdk.gateway";
    static final String MOUNT_ALIAS = "pdk";
    static final Logger LOG = LoggerFactory.getLogger("pdk.gateway");

    private final Token token = new Token();
    private GatewayContext context;
    private ChangeLog changes;
    private final LogTap logs = new LogTap();

    @Override
    public void setup(GatewayContext context) {
        this.context = context;
        this.changes = new ChangeLog(context.getProjectManager(), Sandbox.fromSystemProperties());
    }

    @Override
    public void startup(LicenseState activationState) {
        try {
            token.rotate(context.getSystemManager().getDataDir().toPath());
        } catch (IOException error) {
            throw new IllegalStateException("Cannot write the PDK token file", error);
        }
        try {
            changes.startup();
        } catch (RuntimeException error) {
            LOG.warn("PDK change log did not start; /changes stays empty", error);
        }
        if (!logs.attach()) {
            LOG.warn("PDK log tap not attached; /logs stays empty");
        }
        Sandbox sandbox = Sandbox.fromSystemProperties();
        LOG.info("PDK gateway started: /data/{}/v1, sandbox {}, target {}", MOUNT_ALIAS,
                sandbox.name().orElse("<none: read-only>"), sandbox.target().orElse("<none>"));
        Sandbox.requestedTarget().filter(name -> sandbox.target().isEmpty()).ifPresent(name ->
                LOG.warn("PDK ignores -D{}={}: it needs a sandbox and must differ from it", Sandbox.TARGET_PROPERTY, name));
    }

    @Override
    public void shutdown() {
        token.revoke();
        logs.detach();
        try {
            changes.shutdown();
        } catch (RuntimeException error) {
            LOG.warn("PDK change log did not shut down cleanly", error);
        }
    }

    @Override
    public void mountRouteHandlers(RouteGroup routes) {
        new Routes(context, token, Sandbox.fromSystemProperties(), changes, logs).mount(routes);
    }

    @Override
    public Optional<String> getMountPathAlias() {
        return Optional.of(MOUNT_ALIAS);
    }

    @Override
    public boolean isMakerEditionCompatible() {
        return true;
    }

    @Override
    public boolean isFreeModule() {
        return true;
    }
}
