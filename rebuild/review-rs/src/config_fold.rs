//! `feature_reach.ConfigFold.effective`: the configuration each of a unit's configurations is compared under for its text, from the fold the parent derived once (`build._config_fold`) and the init carries.

use std::collections::HashMap;

use crate::pyfmt::PyValue;
use crate::wire::{Init, Result, WireError, error};

/// The vocabulary name of `feature_reach.DEFAULT`, the configuration a folded one is compared under.
pub const DEFAULT_NAME: &str = "rebuild.review.feature_reach.DEFAULT";

/// The init's `ConfigFold`: each folding configuration's trigger letters, sorted, and `DEFAULT`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ConfigFold {
    default: String,
    triggers: HashMap<String, Vec<char>>,
}

impl ConfigFold {
    /// The fold the init carries, with `DEFAULT` from its vocabulary; a configuration named twice in the triggers is refused, since Python's mapping holds each once.
    pub fn from_init(init: &Init) -> Result<Self> {
        let Some(PyValue::Str(default)) = init.constant(DEFAULT_NAME) else {
            return error(format!("the init's {DEFAULT_NAME} is not a str"));
        };
        let mut triggers = HashMap::new();
        for (config, letters) in &init.fold.triggers {
            let mut letters = letters.clone();
            letters.sort_unstable();
            letters.dedup();
            if triggers.insert(config.clone(), letters).is_some() {
                return Err(WireError(format!(
                    "the fold names the configuration {config} twice"
                )));
            }
        }
        Ok(Self {
            default: default.clone(),
            triggers,
        })
    }

    #[cfg(test)]
    fn new(default: &str, triggers: &[(&str, &str)]) -> Self {
        Self {
            default: default.to_string(),
            triggers: triggers
                .iter()
                .map(|(config, letters)| {
                    let mut letters: Vec<char> = letters.chars().collect();
                    letters.sort_unstable();
                    letters.dedup();
                    ((*config).to_string(), letters)
                })
                .collect(),
        }
    }

    /// `DEFAULT`.
    pub fn default_config(&self) -> &str {
        &self.default
    }

    /// `ConfigFold.effective(configs, text)`: in order, `DEFAULT` for each configuration whose trigger letters `text` holds none of, and the configuration itself otherwise, when `configs` holds `DEFAULT`; `configs` unchanged when it does not.
    pub fn effective(&self, configs: &[String], text: &str) -> Vec<String> {
        if !configs.contains(&self.default) {
            return configs.to_vec();
        }
        configs
            .iter()
            .map(|config| match self.triggers.get(config) {
                Some(letters)
                    if !text
                        .chars()
                        .any(|letter| letters.binary_search(&letter).is_ok()) =>
                {
                    self.default.clone()
                }
                _ => config.clone(),
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn configs(names: &[&str]) -> Vec<String> {
        names.iter().map(|name| (*name).to_string()).collect()
    }

    #[test]
    fn a_configuration_folds_to_default_only_in_a_unit_that_holds_default_and_a_text_without_its_triggers()
     {
        let fold = ConfigFold::new("default", &[("ss03", "\u{e652}"), ("ss10", "")]);
        let unit = configs(&["default", "ss03", "ss04", "ss10"]);
        assert_eq!(
            fold.effective(&unit, "\u{e650}\u{e651}"),
            configs(&["default", "default", "ss04", "default"])
        );
        assert_eq!(
            fold.effective(&unit, "\u{e650}\u{e652}"),
            configs(&["default", "ss03", "ss04", "default"])
        );
        let without_default = configs(&["ss03", "ss10"]);
        assert_eq!(fold.effective(&without_default, "a"), without_default);
    }
}
