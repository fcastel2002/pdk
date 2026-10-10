package dev.pdk.gateway;

import java.util.LinkedHashMap;
import java.util.Map;

/** An expected failure, rendered as {"error": CODE, "message": ..., ...extra} with an HTTP status. */
final class ApiError extends RuntimeException {
    private static final long serialVersionUID = 1L;

    final int status;
    final String code;
    private final transient Map<String, Object> extra;

    ApiError(int status, String code, String message) {
        this(status, code, message, Map.of());
    }

    ApiError(int status, String code, String message, Map<String, Object> extra) {
        super(message, null, false, false);
        this.status = status;
        this.code = code;
        this.extra = extra;
    }

    Map<String, Object> body() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", code);
        body.put("message", getMessage());
        body.putAll(extra);
        return body;
    }
}
