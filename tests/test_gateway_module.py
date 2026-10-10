"""The pdk-gateway build: version stamp, module.xml, javac arguments, a real build and the Java SelfTest."""

from __future__ import annotations

import io
import os
import re
import subprocess
import zipfile
from datetime import datetime

import pytest

from pdk.bundle import find_ignition_home
from pdk.gateway import module


def test_module_version_fits_ignition_build_number() -> None:
    version = module.module_version(datetime(2026, 12, 31, 23, 59))
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d{9}", version)
    assert int(version.rsplit(".", 1)[1]) < 2**31
    assert module.module_version(datetime(2026, 10, 4, 15, 52)) < module.module_version(datetime(2026, 10, 4, 15, 53))


def test_module_xml_declares_a_free_module_that_needs_perspective() -> None:
    xml = module.render_module_xml("0.1.0.262771552")
    assert "@" not in xml
    assert "<id>dev.pdk.gateway</id>" in xml and "<freeModule>true</freeModule>" in xml
    assert '<depends scope="G" required="true">com.inductiveautomation.perspective</depends>' in xml
    assert f'<jar scope="G">{module.JAR_FILE}</jar>' in xml


def test_argfile_quotes_every_argument() -> None:
    text = module.argfile_text(["-d", "C:\\Program Files\\x", 'a"b'])
    assert text.splitlines() == ['"-d"', '"C:/Program Files/x"', '"a\\"b"']


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    home = find_ignition_home()
    if home is None:
        pytest.skip("no local Ignition install")
    try:
        module.find_javac()
    except module.ModuleError as exc:
        pytest.skip(str(exc))
    out = tmp_path_factory.mktemp("gateway-build")
    return home, out, module.build(home, out)


def test_build_packages_java17_classes(built) -> None:
    _, _, result = built
    with zipfile.ZipFile(result.modl) as modl:
        assert sorted(modl.namelist()) == sorted(["module.xml", module.JAR_FILE])
        assert result.version in modl.read("module.xml").decode("utf-8")
        jar = zipfile.ZipFile(io.BytesIO(modl.read(module.JAR_FILE)))
    hook = jar.read("dev/pdk/gateway/GatewayHook.class")
    assert int.from_bytes(hook[6:8], "big") == 61  # class file major version of Java 17


def test_java_selftest(built) -> None:
    home, out, _ = built
    classpath = [out / "classes", *module.compile_classpath(home, out / "sdk")]
    tests = out / "test-classes"
    module.compile_java(sorted((module.GATEWAY_DIR / "src" / "test" / "java").rglob("*.java")), tests, classpath)
    java = module.find_javac().with_name("java.exe" if os.name == "nt" else "java")
    argfile = out / "selftest.args"
    argfile.write_text(module.argfile_text(["-cp", os.pathsep.join(p.as_posix() for p in [tests, *classpath]),
                                            "dev.pdk.gateway.SelfTest"]), encoding="utf-8")
    result = subprocess.run([str(java), f"@{argfile}"], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SelfTest OK" in result.stdout
