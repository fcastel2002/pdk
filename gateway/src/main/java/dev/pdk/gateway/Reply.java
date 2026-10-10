package dev.pdk.gateway;

/** A route result: HTTP status plus the object rendered as JSON. */
record Reply(int status, Object body) {
    static Reply ok(Object body) {
        return new Reply(200, body);
    }
}
