//! Compiles HarfBuzz into the crate (`rebuild/CORPUS-UNITS-PORT-PLAN.md` § HarfBuzz and outlines): the amalgamated `src/harfbuzz.cc` of the release uharfbuzz links, as one C++11 translation unit with `HAVE_PTHREAD` and no platform backend.
//!
//! When `target/vendor/` lacks the unpacked source, the release tarball is fetched from HarfBuzz's GitHub releases with `curl`, refused unless its sha256 is [`TARBALL_SHA256`], and its `src/` unpacked there with `tar`, without HarfBuzz's own Python scripts, since pyright checks every `.py` file under `rebuild/`, ignored or not. A `uv.lock` bump of uharfbuzz moves [`VERSION`] and the digest together.

use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::process::Command;

use sha2::Digest as _;

/// The HarfBuzz version the locked uharfbuzz links (`uharfbuzz.version_string()`).
const VERSION: &str = "12.3.2";
/// The sha256 of the release tarball `harfbuzz-12.3.2.tar.xz`.
const TARBALL_SHA256: &str = "6f6db164359a2da5a84ef826615b448b33e6306067ad829d85d5b0bf936f1bb8";

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    let vendor = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("vendor");
    let source = vendor.join(format!("harfbuzz-{VERSION}"));
    let amalgamation = source.join("src").join("harfbuzz.cc");
    if !amalgamation.is_file()
        && let Err(message) = fetch(&vendor, &source)
    {
        panic!("cannot provide the HarfBuzz {VERSION} source: {message}");
    }
    println!("cargo:rerun-if-changed={}", amalgamation.display());
    cc::Build::new()
        .cpp(true)
        .std("c++11")
        .warnings(false)
        .define("HAVE_PTHREAD", "1")
        .include(source.join("src"))
        .file(&amalgamation)
        .compile("harfbuzz");
}

fn fetch(vendor: &Path, source: &Path) -> Result<(), String> {
    std::fs::create_dir_all(vendor)
        .map_err(|error| format!("cannot create {}: {error}", vendor.display()))?;
    let name = format!("harfbuzz-{VERSION}.tar.xz");
    let tarball = vendor.join(&name);
    if !tarball.is_file() {
        let partial = vendor.join(format!("{name}.part"));
        let url =
            format!("https://github.com/harfbuzz/harfbuzz/releases/download/{VERSION}/{name}");
        run(Command::new("curl")
            .args([
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--output",
            ])
            .arg(&partial)
            .arg(&url))?;
        std::fs::rename(&partial, &tarball)
            .map_err(|error| format!("cannot move {} into place: {error}", partial.display()))?;
    }
    let bytes = std::fs::read(&tarball)
        .map_err(|error| format!("cannot read {}: {error}", tarball.display()))?;
    let mut digest = String::with_capacity(64);
    for byte in sha2::Sha256::digest(&bytes) {
        write!(digest, "{byte:02x}").expect("a String takes every write");
    }
    if digest != TARBALL_SHA256 {
        return Err(format!(
            "{} has the sha256 {digest}, not {TARBALL_SHA256}; delete it to fetch it again",
            tarball.display()
        ));
    }
    let unpacking = vendor.join(format!("harfbuzz-{VERSION}.unpacking"));
    if unpacking.exists() {
        std::fs::remove_dir_all(&unpacking)
            .map_err(|error| format!("cannot clear {}: {error}", unpacking.display()))?;
    }
    std::fs::create_dir_all(&unpacking)
        .map_err(|error| format!("cannot create {}: {error}", unpacking.display()))?;
    run(Command::new("tar")
        .arg("-xf")
        .arg(&tarball)
        .arg("-C")
        .arg(&unpacking)
        .args(["--exclude", "*.py"])
        .arg(format!("harfbuzz-{VERSION}/src")))?;
    std::fs::rename(unpacking.join(format!("harfbuzz-{VERSION}")), source)
        .map_err(|error| format!("the tarball does not unpack to harfbuzz-{VERSION}/: {error}"))?;
    std::fs::remove_dir_all(&unpacking)
        .map_err(|error| format!("cannot remove {}: {error}", unpacking.display()))
}

fn run(command: &mut Command) -> Result<(), String> {
    let finished = command
        .output()
        .map_err(|error| format!("cannot run {command:?}: {error}"))?;
    if finished.status.success() {
        return Ok(());
    }
    Err(format!(
        "{command:?} exited {}: {}",
        finished.status,
        String::from_utf8_lossy(&finished.stderr).trim()
    ))
}
