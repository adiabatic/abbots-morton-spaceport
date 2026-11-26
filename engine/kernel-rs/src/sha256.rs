//! SHA-256 for the deep-class ids (`fixpoint::deep_class_id`) and `artifacts::table_digest`, through RustCrypto's `sha2`. Both must match Python's `hashlib.sha256` output.
//!
//! The incremental state hashes a table a row at a time without building the whole message. `digest_hex` hashes one message in a single call; both interfaces return lowercase hexadecimal.

use sha2::Digest as _;
use std::fmt::Write as _;

/// One message's digest as 64 lowercase hex digits, the format of `hashlib.sha256(...).hexdigest()`.
pub fn digest_hex(message: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(message);
    digest.finish()
}

#[derive(Default)]
pub struct Sha256(sha2::Sha256);

impl Sha256 {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn update(&mut self, message: &[u8]) {
        self.0.update(message);
    }

    pub fn finish(self) -> String {
        let mut out = String::with_capacity(64);
        for byte in self.0.finalize() {
            write!(&mut out, "{byte:02x}").unwrap();
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixpoint::deep_class_id;

    /// The wrapper accepts fragmented application input and empty updates; the expected digests are computed independently with Python's `hashlib`.
    #[test]
    fn streamed_member_lists_take_the_digest_python_computes() {
        let message = [
            "qsPea", "qsTea", "qsKey", "qsBay", "qsDay", "qsGay", "qsFee", "qsThaw", "qsSee",
            "qsShe", "qsVie", "qsThey", "qsZoo", "qsJai", "qsCheer", "qsJay", "qsMay", "qsNo",
            "qsLow", "qsRoe", "qsWay", "qsYe", "qsHe", "qsIt",
        ]
        .join("\t");
        let expected = "28e671428535dcf52265b225b4b8e4e4b41e39abc5a340a2975edae966f16a6e";
        assert_eq!(digest_hex(message.as_bytes()), expected);
        for width in [1, 7, 55, 56, 63, 64, 65] {
            let mut digest = Sha256::new();
            digest.update(b"");
            for chunk in message.as_bytes().chunks(width) {
                digest.update(chunk);
                digest.update(b"");
            }
            assert_eq!(digest.finish(), expected, "chunk width {width}");
        }
        assert_eq!(
            Sha256::default().finish(),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    /// A class id is `#C` plus the first twelve hex digits of the SHA-256 of the tab-joined member list. The expected values were computed with Python's `hashlib`, outside this crate. The ids are written into the transitions stream, so they must match Python's, and comparing the crate with itself would check nothing.
    #[test]
    fn a_tab_joined_member_list_takes_the_class_id_python_computes() {
        let members = |names: &[&str]| -> Vec<String> {
            names.iter().map(|name| (*name).to_owned()).collect()
        };
        assert_eq!(deep_class_id(&members(&[])), "#Ce3b0c44298fc");
        assert_eq!(deep_class_id(&members(&["qsPea"])), "#C55e3af9ab7e8");
        assert_eq!(
            deep_class_id(&members(&["qsPea", "qsTea"])),
            "#C6bcf85c3d950"
        );
        assert_eq!(
            deep_class_id(&members(&["qsTea", "qsPea"])),
            "#Cece81054052f",
            "the id addresses the members in the order it is handed them, which is why the emission sorts them first"
        );
        assert_eq!(
            digest_hex(b"qsPea\tqsTea"),
            "6bcf85c3d9502637153a67b448cdd8facc82698dcf7064a78d0168cdc9ad0ba8",
            "and the id above is the first twelve digits of exactly this digest"
        );
    }
}
