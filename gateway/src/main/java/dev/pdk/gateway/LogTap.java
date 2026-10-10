package dev.pdk.gateway;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.LoggerContext;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.classic.spi.IThrowableProxy;
import ch.qos.logback.classic.spi.StackTraceElementProxy;
import ch.qos.logback.core.AppenderBase;
import org.slf4j.LoggerFactory;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Keeps the last WARN/ERROR events of the gateway in memory, with their MDC (Perspective sets session, view and
 * component there), for /logs. Never logs from append(): that would recurse into itself.
 */
final class LogTap extends AppenderBase<ILoggingEvent> {
    private static final int CAPACITY = 1000;
    private static final int MAX_FRAMES = 8;
    private static final int MAX_TEXT = 2000;

    private final Deque<Map<String, Object>> events = new ArrayDeque<>();
    private long seq;
    private boolean attached;

    LogTap() {
        setName("pdk-logtap");
    }

    /** Attaches to the root logger; returns false when the gateway does not log through logback. */
    synchronized boolean attach() {
        if (attached) {
            return true;
        }
        try {
            if (!(LoggerFactory.getILoggerFactory() instanceof LoggerContext logging)) {
                return false;
            }
            setContext(logging);
            start();
            logging.getLogger(Logger.ROOT_LOGGER_NAME).addAppender(this);
            attached = true;
            return true;
        } catch (RuntimeException | LinkageError error) {
            return false;
        }
    }

    synchronized void detach() {
        if (!attached) {
            return;
        }
        try {
            if (getContext() instanceof LoggerContext logging) {
                logging.getLogger(Logger.ROOT_LOGGER_NAME).detachAppender(this);
            }
            stop();
        } finally {
            attached = false;
        }
    }

    synchronized boolean isAttached() {
        return attached;
    }

    @Override
    protected void append(ILoggingEvent event) {
        if (!event.getLevel().isGreaterOrEqual(Level.WARN)) {
            return;
        }
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("time", event.getTimeStamp());
        entry.put("level", event.getLevel().toString());
        entry.put("logger", event.getLoggerName());
        entry.put("thread", event.getThreadName());
        entry.put("message", clip(event.getFormattedMessage()));
        Map<String, String> mdc = event.getMDCPropertyMap();
        if (mdc != null && !mdc.isEmpty()) {
            entry.put("mdc", new LinkedHashMap<>(mdc));
        }
        IThrowableProxy thrown = event.getThrowableProxy();
        if (thrown != null) {
            entry.put("error", throwable(thrown));
        }
        synchronized (this) {
            entry.put("seq", ++seq);
            events.addLast(entry);
            while (events.size() > CAPACITY) {
                events.removeFirst();
            }
        }
    }

    /** Events after `after`; `project` and `view` (a resource path) filter on the MDC Perspective sets. */
    synchronized Map<String, Object> since(long after, int limit, String project, String view) {
        List<Object> items = new ArrayList<>();
        for (Map<String, Object> entry : events) {
            if ((long) entry.get("seq") <= after) {
                continue;
            }
            if (!matches(entry, project, view)) {
                continue;
            }
            if (items.size() >= Math.max(1, Math.min(limit, CAPACITY))) {
                break;
            }
            items.add(entry);
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("attached", attached);
        body.put("last", seq);
        body.put("events", items);
        return body;
    }

    static boolean matches(Map<String, Object> entry, String project, String view) {
        if (project == null && view == null) {
            return true;
        }
        if (!(entry.get("mdc") instanceof Map<?, ?> mdc)) {
            return false;
        }
        if (project != null && !project.equals(mdc.get("project-name"))) {
            return false;
        }
        Object instance = mdc.get("view");
        return view == null || view.equals(instance) || (instance instanceof String text && text.startsWith(view + "@"));
    }

    private static Map<String, Object> throwable(IThrowableProxy thrown) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("class", thrown.getClassName());
        result.put("message", clip(thrown.getMessage()));
        List<String> frames = new ArrayList<>();
        StackTraceElementProxy[] stack = thrown.getStackTraceElementProxyArray();
        if (stack != null) {
            for (int i = 0; i < stack.length && i < MAX_FRAMES; i++) {
                frames.add(stack[i].getSTEAsString());
            }
        }
        result.put("frames", frames);
        if (thrown.getCause() != null) {
            result.put("cause", throwable(thrown.getCause()));
        }
        return result;
    }

    private static String clip(String text) {
        if (text == null || text.length() <= MAX_TEXT) {
            return text;
        }
        return text.substring(0, MAX_TEXT) + "…";
    }
}
