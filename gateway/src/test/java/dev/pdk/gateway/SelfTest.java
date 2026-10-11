package dev.pdk.gateway;

import com.inductiveautomation.ignition.common.gson.JsonParser;
import com.inductiveautomation.ignition.common.resourcecollection.ResourcePath;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

/** Offline checks of the module's own logic (no gateway). Run by tests/test_gateway_module.py. */
public final class SelfTest {
    private SelfTest() {
    }

    public static void main(String[] args) throws Exception {
        loopback();
        sandbox();
        targets();
        token();
        writeRequests();
        ancestors();
        changeLog();
        qualified();
        logFilter();
        tags();
        System.out.println("SelfTest OK");
    }

    private static void loopback() {
        for (String address : new String[]{"127.0.0.1", "127.0.0.2", "::1", "0:0:0:0:0:0:0:1", "[::1]", "::ffff:127.0.0.1"}) {
            check(Access.isLoopback(address), "loopback: " + address);
        }
        for (String address : new String[]{null, "", "192.168.1.10", "10.0.0.1", "::2", "localhost", "127.0.0.1.evil"}) {
            check(!Access.isLoopback(address), "not loopback: " + address);
        }
    }

    private static void sandbox() {
        Sandbox none = Sandbox.of(null);
        check(!none.writeEnabled() && !Sandbox.of("  ").writeEnabled(), "no property: read-only");
        expect("WRITE_DISABLED", () -> none.requireWritable("pdk_sandbox"));
        Sandbox sandbox = Sandbox.of(" pdk_sandbox ");
        sandbox.requireWritable("pdk_sandbox");
        expect("OUTSIDE_SANDBOX", () -> sandbox.requireWritable("Production"));
        expect("OUTSIDE_SANDBOX", () -> sandbox.requireWritable("Pdk_sandbox"));

        Target finished = Target.parse("com.inductiveautomation.perspective/views/Planta/Resumen");
        Target work = Target.parse("com.inductiveautomation.perspective/views/PDK/Resumen");
        Target pages = Target.parse("com.inductiveautomation.perspective/page-config");
        check(sandbox.target().isEmpty(), "no target by default");
        sandbox.requireWritable("pdk_sandbox", pages);
        expect("OUTSIDE_SANDBOX", () -> sandbox.requireWritable("Planta", finished));

        Sandbox both = Sandbox.of("pdk_sandbox", " Planta ");
        check(both.target().orElseThrow().equals("Planta"), "target set");
        both.requireWritable("pdk_sandbox", work);
        both.requireWritable("Planta", finished);
        expect("OUTSIDE_TARGET", () -> both.requireWritable("Planta", work));
        expect("OUTSIDE_TARGET", () -> both.requireWritable("Planta", Target.parse("com.inductiveautomation.perspective/views/PDK")));
        expect("OUTSIDE_TARGET", () -> both.requireWritable("Planta", Target.parse("com.inductiveautomation.perspective/views")));
        expect("OUTSIDE_TARGET", () -> both.requireWritable("Planta", pages));
        both.requireWritable("Planta", Target.parse("com.inductiveautomation.perspective/stylesheet"));
        expect("OUTSIDE_TARGET", () -> both.requireWritable("Planta", Target.parse("com.inductiveautomation.perspective/stylesheet/x")));
        both.requireWritable("Planta", Target.parse("com.inductiveautomation.perspective/views/PDKX/Resumen"));
        both.requireWritable("Planta", Target.parse("com.inductiveautomation.perspective/style-classes/Card"));
        both.requireWritable("Planta", Target.parse("ignition/script-python/util/format"));
        expect("OUTSIDE_TARGET", () -> both.requireWritable("Planta",
                Target.parse("com.inductiveautomation.perspective/style-classes/PDK/Card")));
        expect("OUTSIDE_TARGET", () -> both.requireWritable("Planta", Target.parse("ignition/script-python/PDK/util")));
        expect("OUTSIDE_TARGET", () -> both.requireWritable("Planta", Target.parse("ignition/named-query/q")));
        expect("OUTSIDE_SANDBOX", () -> both.requireWritable("Planta"));
        expect("OUTSIDE_SANDBOX", () -> both.requireWritable("Otro", finished));
        check(Sandbox.of("pdk_sandbox", "pdk_sandbox").target().isEmpty(), "target equal to sandbox is ignored");
        check(Sandbox.of(null, "Planta").target().isEmpty(), "target without sandbox is ignored");
        expect("WRITE_DISABLED", () -> Sandbox.of(null, "Planta").requireWritable("Planta", finished));
    }

    private static void targets() {
        Target view = Target.parse("com.inductiveautomation.perspective/views/PDK/Menu Horizontal/");
        check(view.typeKey().equals("com.inductiveautomation.perspective/views"), "type key");
        check(view.path().equals("PDK/Menu Horizontal"), "nested path with a space");
        check(Target.parse("com.inductiveautomation.perspective/page-config").path().isEmpty(), "singleton");
        for (String bad : new String[]{"", "views", "m/t/../x", "m/t/a//b", "m/t/ a", "m/t/a:b", "m/t/a\\b", "m t/x/y"}) {
            expect("INVALID_PATH", () -> Target.parse(bad));
        }
        Target folder = Target.parse("m/t/PDK");
        check(folder.contains(new ResourcePath(folder.type(), "PDK/Spike")), "child is contained");
        check(!folder.contains(new ResourcePath(folder.type(), "PDKX/Spike")), "sibling prefix is not contained");
        check(!folder.contains(new ResourcePath(folder.type(), "PDK")), "a folder does not contain itself");
        check(Target.parse("m/t").contains(new ResourcePath(folder.type(), "PDK")), "type root contains everything");
    }

    private static void token() throws Exception {
        Path directory = Files.createTempDirectory("pdk-token");
        Token token = new Token();
        check(!token.matches("anything"), "no token before startup");
        token.rotate(directory);
        String value = Files.readString(directory.resolve(Token.FILE_NAME), StandardCharsets.UTF_8);
        check(value.length() >= 40 && token.matches(value) && token.matches(value + "\n"), "token file matches");
        check(!Files.exists(directory.resolve(Token.FILE_NAME + ".tmp")), "no temporary token file left");
        check(!token.matches(value.substring(1)) && !token.matches(null), "wrong token rejected");
        token.rotate(directory);
        check(!token.matches(value), "rotation invalidates the old token");
        token.revoke();
        check(!Files.exists(directory.resolve(Token.FILE_NAME)) && !token.matches(value), "revoke deletes the file");
        Files.deleteIfExists(directory);
    }

    private static void writeRequests() {
        Resources.WriteRequest request = Resources.WriteRequest.parse(
                "{\"files\": {\"view.json\": \"{\\\"root\\\": {}}\"}, \"signature\": \"ABcd\"}");
        check(request.files().equals(Map.of("view.json", "{\"root\": {}}")), "files");
        check(request.signature().equals("abcd") && request.scope().equals("G"), "signature and default scope");
        check(Resources.WriteRequest.parse("{\"files\": {\"a.json\": \"x\"}, \"signature\": null}").signature() == null,
                "null signature means create");
        expect("BAD_REQUEST", () -> Resources.WriteRequest.parse("not json"));
        expect("BAD_REQUEST", () -> Resources.WriteRequest.parse("{\"files\": {}}"));
        expect("BAD_REQUEST", () -> Resources.WriteRequest.parse("{\"files\": {\"view.json\": 1}}"));
        expect("INVALID_FILE_NAME", () -> Resources.WriteRequest.parse("{\"files\": {\"resource.json\": \"x\"}}"));
        expect("INVALID_FILE_NAME", () -> Resources.WriteRequest.parse("{\"files\": {\"../x.json\": \"x\"}}"));
        expect("BAD_SIGNATURE", () -> Resources.WriteRequest.parse("{\"files\": {\"a.json\": \"x\"}, \"signature\": \"xyz\"}"));
        expect("BAD_SIGNATURE", () -> Resources.normalizeSignature("abc"));
        check(Resources.WriteRequest.checkScope("gcd").equals("GCD") && Resources.WriteRequest.checkScope("A").equals("A"),
                "scope codes");
        expect("BAD_REQUEST", () -> Resources.WriteRequest.checkScope("GG"));
        expect("BAD_REQUEST", () -> Resources.WriteRequest.checkScope("X"));
        expect("BAD_REQUEST", () -> Resources.WriteRequest.parse("{\"files\": {\"a.json\": \"x\"}, \"scope\": \"Z\"}"));
    }

    private static void ancestors() {
        check(Resources.ancestors("PDK/_selftest/Spike a").equals(java.util.List.of("PDK/_selftest", "PDK")), "ancestors");
        check(Resources.ancestors("Top").isEmpty() && Resources.ancestors("").isEmpty(), "no ancestors at the root");
    }

    @SuppressWarnings("unchecked")
    private static void changeLog() {
        ChangeLog log = new ChangeLog(null, Sandbox.of("pdk_sandbox"));
        ResourcePath view = new ResourcePath(new com.inductiveautomation.ignition.common.resourcecollection.ResourceType(
                ChangeLog.PERSPECTIVE, "views"), "PDK/A");
        log.record("pdk_sandbox", "modified", view, Resources.ORIGIN, java.util.Optional.empty());
        log.record("pdk_sandbox", "modified", view, new Object(), java.util.Optional.empty());
        java.util.List<Map<String, Object>> events = (java.util.List<Map<String, Object>>) log.since(0, 10).get("events");
        check(events.size() == 2 && "pdk".equals(events.get(0).get("origin"))
                && "external".equals(events.get(1).get("origin")), "origin by push context");
        check("com.inductiveautomation.perspective/views".equals(events.get(0).get("type"))
                && "PDK/A".equals(events.get(0).get("path")), "event address");
        check(((java.util.List<?>) log.since(1, 10).get("events")).size() == 1, "since filters by sequence");
        for (int i = 0; i < ChangeLog.CAPACITY; i++) {
            log.record("pdk_sandbox", "created", view, null, java.util.Optional.empty());
        }
        Map<String, Object> page = log.since(0, 5);
        check(Boolean.TRUE.equals(page.get("truncated")) && ((java.util.List<?>) page.get("events")).size() == 5
                && (long) page.get("seq") == ChangeLog.CAPACITY + 2, "bounded buffer reports truncation");
        check(Boolean.FALSE.equals(log.since(ChangeLog.CAPACITY, 5).get("truncated")), "recent cursor is complete");
    }

    private static void qualified() {
        String raw = "{\"$\":[\"map\",0,1],"
                + "\"text\":{\"$\":[\"qv\",[-2147483129,\"Path not found.\"],2],\"$v\":null},"
                + "\"ok\":{\"$\":[\"qv\",192,2],\"$v\":\"OK 2\"},"
                + "\"style\":{\"$\":[\"map\",0,1],\"color\":{\"$\":[\"qv\",0,1],\"$v\":\"red\"}},"
                + "\"items\":{\"$\":[\"array\",0,1],\"$a\":[{\"$\":[\"qv\",0,1],\"$v\":1},"
                + "{\"$\":[\"qv\",[-1073741045,\"Traceback\"],1],\"$v\":null}]}}";
        java.util.List<Map<String, Object>> issues = new java.util.ArrayList<>();
        com.inductiveautomation.ignition.common.gson.JsonElement plain = Qualified.plain(
                JsonParser.parseString(raw), "props", issues);
        check(plain.toString().equals(
                "{\"text\":null,\"ok\":\"OK 2\",\"style\":{\"color\":\"red\"},\"items\":[1,null]}"),
                "plain form: " + plain);
        check(issues.size() == 2, "two non-Good qualities: " + issues);
        check("props.text".equals(issues.get(0).get("path")) && "Bad_NotFound".equals(issues.get(0).get("quality"))
                && "Path not found.".equals(issues.get(0).get("message")), "bad quality with message: " + issues.get(0));
        check("props.items[1]".equals(issues.get(1).get("path"))
                && "Error_ScriptEval".equals(issues.get(1).get("quality")), "error inside an array: " + issues.get(1));
    }

    private static void logFilter() {
        Map<String, Object> entry = Map.of("mdc", Map.of("project-name", "pdk_sandbox",
                "view", "PDK/_eyes/Defects@C", "component", "root/Healthy"));
        check(LogTap.matches(entry, null, null), "no filter matches");
        check(LogTap.matches(entry, "pdk_sandbox", "PDK/_eyes/Defects"), "project and view match");
        check(!LogTap.matches(entry, "other", null), "other project");
        check(!LogTap.matches(entry, "pdk_sandbox", "PDK/_eyes/Def"), "view prefix is not enough");
        check(!LogTap.matches(Map.of("logger", "x"), "pdk_sandbox", null), "no MDC, filtered out");
    }

    private static void tags() {
        String provider = "default";
        check(Tags.parse("PDK/Sandbox/Setpoint", provider).toStringFull().equals("[default]PDK/Sandbox/Setpoint"),
                "tags: provider added");
        check(Tags.parse("[Other]Planta/X", provider).getSource().equals("Other"), "tags: explicit provider kept");
        expect("INVALID_TAG_PATH", () -> Tags.parse("  ", provider));
        Tags.requireWork(Tags.parse("PDK", provider), provider);
        Tags.requireWork(Tags.parse("pdk/Sandbox/Setpoint", provider), provider);
        Tags.requireWork(Tags.parse("[DEFAULT]PDK/X", provider), provider);
        for (String outside : new String[]{"Planta/X", "[Other]PDK/X", "PDKX/Y", "PDK/X.ValueSource", "PDK/../Planta"}) {
            expect("OUTSIDE_TAG_FOLDER", () -> {
                try {
                    Tags.requireWork(Tags.parse(outside, provider), provider);
                } catch (ApiError error) {
                    throw error.code.equals("INVALID_TAG_PATH")
                            ? new ApiError(403, "OUTSIDE_TAG_FOLDER", error.getMessage()) : error;
                }
            });
        }
        check(Tags.under("PDK/North/Level", "PDK") && Tags.under("pdk/north", "PDK/North"), "tags: under");
        check(!Tags.under("PDKX/Level", "PDK") && !Tags.under("PDK/North2", "PDK/North"), "tags: not under");

        String memory = "{\"name\":\"Setpoint\",\"tagType\":\"AtomicTag\",\"valueSource\":\"memory\",\"dataType\":\"Float8\","
                + "\"value\":20,\"alarms\":[{\"name\":\"High\",\"mode\":\"AboveValue\",\"setpointA\":80,"
                + "\"priority\":\"High\",\"displayPath\":\"North Area/Tank1\"}]}";
        check(Tags.checkImport(memory) == 1, "tags: one memory tag");
        check(Tags.checkImport("{\"tags\":[{\"name\":\"Sandbox\",\"tagType\":\"Folder\",\"tags\":[" + memory + ","
                + memory.replace("Setpoint", "Message") + "]}]}") == 2, "tags: folder with two tags");
        String[] rejected = {
            "[]", "{\"tags\":{}}", "{\"name\":\"X\",\"tags\":[]}",
            "{\"name\":\"X\",\"tagType\":\"AtomicTag\",\"valueSource\":\"opc\"}",
            "{\"name\":\"X\",\"tagType\":\"AtomicTag\"}",
            "{\"name\":\"X\",\"tagType\":\"UdtInstance\",\"typeId\":\"Motor\"}",
            "{\"name\":\"a/b\",\"tagType\":\"Folder\"}",
            "{\"name\":\"..\",\"tagType\":\"Folder\"}",
            memory.replace("\"priority\"", "\"activePipeline\":\"Email\",\"priority\""),
            memory.replace("\"value\":20", "\"value\":20,\"eventScripts\":[]"),
            memory.replace("\"setpointA\":80", "\"setpointA\":{\"bindType\":\"Expression\",\"value\":\"runScript('x')\"}"),
            "{\"name\":\"F\",\"tagType\":\"Folder\",\"tags\":[{\"name\":\"X\",\"tagType\":\"AtomicTag\",\"valueSource\":\"reference\"}]}",
        };
        for (String body : rejected) {
            expect(body.equals("[]") ? "BAD_REQUEST" : "INVALID_TAGS", () -> Tags.checkImport(body));
        }
        check(Tags.checkImport(memory.replace("\"value\":20", "\"value\":20,\"historyEnabled\":false")) == 1,
                "tags: historyEnabled false is fine");
        check(Tags.checkImport(memory.replace("\"value\":20",
                "\"value\":20,\"historyEnabled\":true,\"historyProvider\":\"Core Historian\"")) == 1,
                "tags: history may be enabled");

        String member = "{\"name\":\"Speed\",\"tagType\":\"AtomicTag\",\"valueSource\":\"memory\",\"dataType\":\"Float8\","
                + "\"documentation\":{\"bindType\":\"parameter\",\"binding\":\"{Area}\"}}";
        String type = "{\"name\":\"Motor\",\"tagType\":\"UdtType\",\"parameters\":{\"Area\":{\"dataType\":\"String\","
                + "\"value\":\"North\"}},\"tags\":[" + member + "]}";
        check(Tags.checkImport(type, true) == 1, "tags: a UDT definition with a memory member");
        expect("INVALID_TAGS", () -> Tags.checkImport(type, false));
        expect("INVALID_TAGS", () -> Tags.checkImport(memory, true));
        expect("INVALID_TAGS", () -> Tags.checkImport(type.replace("\"memory\"", "\"opc\""), true));
        String instance = "{\"name\":\"M1\",\"tagType\":\"UdtInstance\",\"typeId\":\"PDK/Motor\",\"parameters\":"
                + "{\"Area\":\"South\"},\"tags\":[{\"name\":\"Speed\",\"tagType\":\"AtomicTag\",\"value\":5}]}";
        check(Tags.checkImport(instance) == 1, "tags: an instance of a PDK UDT, with a member override");
        expect("INVALID_TAGS", () -> Tags.checkImport(instance.replace("PDK/Motor", "Motor")));
        expect("INVALID_TAGS", () -> Tags.checkImport(instance.replace("PDK/Motor", "PDK/../Motor")));
        expect("INVALID_TAGS", () -> Tags.checkImport(instance.replace("\"value\":5", "\"valueSource\":\"opc\"")));
        check(Tags.ownType("pdk/Pumps/Motor") && !Tags.ownType("PDK/") && !Tags.ownType("Other/Motor"), "tags: own types");
        check(Tags.inTypes(Tags.parse("_types_/PDK/Motor", "default"), "default"), "tags: in _types_/PDK");
        check(!Tags.inTypes(Tags.parse("_types_/Motor", "default"), "default"), "tags: other types");
        check(!Tags.inTypes(Tags.parse("PDK/Motor", "default"), "default"), "tags: work is not types");
        StringBuilder many = new StringBuilder("{\"tags\":[");
        for (int i = 0; i <= Tags.MAX_IMPORT; i++) {
            many.append(i == 0 ? "" : ",").append(memory.replace("Setpoint", "T" + i));
        }
        expect("TOO_MANY_TAGS", () -> Tags.checkImport(many.append("]}").toString()));

        check(Tags.quality("Bad_Stale").getName().equals("Bad_Stale"), "tags: quality by name");
        expect("UNKNOWN_QUALITY", () -> Tags.quality("Pretty_Bad"));
        check(Long.valueOf(42).equals(Tags.value(JsonParser.parseString("42"))),
                "tags: integer value");
        check(Double.valueOf(42.5).equals(Tags.value(JsonParser.parseString("42.5"))),
                "tags: decimal value");
        check(Boolean.TRUE.equals(Tags.value(JsonParser.parseString("true"))),
                "tags: boolean value");
        check(Tags.value(null) == null, "tags: null value");
        expect("BAD_REQUEST", () -> Tags.value(JsonParser.parseString("[1]")));
    }

    private static void expect(String code, Runnable action) {
        try {
            action.run();
        } catch (ApiError error) {
            check(error.code.equals(code), "expected " + code + ", got " + error.code + ": " + error.getMessage());
            return;
        }
        throw new AssertionError("expected " + code + ", nothing was thrown");
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
