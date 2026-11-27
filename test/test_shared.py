from pathlib import Path
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parent.parent


def test_mono_preview_resets_to_regular_weight():
    script = textwrap.dedent("""
        import assert from 'node:assert/strict';
        import { initToggles } from './site/shared.js';

        function createElement() {
          return {
            textContent: '',
            hidden: false,
            listeners: new Map(),
            addEventListener(type, listener) {
              this.listeners.set(type, listener);
            },
            click() {
              this.listeners.get('click')?.();
            },
            querySelector() {
              return null;
            },
          };
        }

        const style = new Map();
        const elements = new Map();

        function register(id) {
          const element = createElement();
          elements.set(id, element);
          return element;
        }

        const fontToggle = register('font-toggle');
        const levelToggle = register('level-toggle');
        const weightToggle = register('weight-toggle');
        const title = createElement();

        globalThis.document = {
          getElementById(id) {
            return elements.get(id) ?? null;
          },
          querySelector(selector) {
            if (selector === 'h1') {
              return title;
            }
            return null;
          },
          documentElement: {
            style: {
              setProperty(name, value) {
                style.set(name, value);
              },
            },
          },
        };

        const toggles = initToggles({
          fontToggle: 'font-toggle',
          levelToggle: 'level-toggle',
          weightToggle: 'weight-toggle',
          titleEl: 'h1',
        });

        assert.equal(style.get('--font-weight'), '400');
        assert.equal(weightToggle.textContent, 'Regular');

        weightToggle.click();
        assert.equal(style.get('--font-weight'), '700');
        assert.equal(weightToggle.textContent, 'Bold');
        assert.equal(toggles.getState().weightIndex, 1);

        fontToggle.click();
        assert.equal(style.get('--font-weight'), '400');
        assert.equal(toggles.getState().weightIndex, 1);

        fontToggle.click();
        assert.equal(style.get('--font-weight'), '700');
        assert.equal(weightToggle.textContent, 'Bold');
        """)

    subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        cwd=ROOT,
    )


def test_site_type_preview_module_is_the_review_apps():
    """The site cannot import from `engine/`, so `site/type-preview.js` is a copy of the review app's `preview.js`, whose tests are `engine/review/jstests/preview.test.js`. Edit the review app's file and copy it over."""
    site_copy = ROOT / "site" / "type-preview.js"
    review_module = ROOT / "engine" / "review" / "static" / "preview.js"
    assert site_copy.read_bytes() == review_module.read_bytes()


def test_overview_page_describes_the_readmes_stylistic_sets():
    """The Overview page's preview box describes each stylistic set by hand, in shorter wording than README's "Stylistic sets" section, so only the list of sets is compared. Adding or removing a set in the README fails this test until the page's rows change with it."""
    import re

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Stylistic sets", 1)[1].split("\n## ", 1)[0]
    readme_sets = re.findall(r"^- `(ss\d+)`:", section, re.MULTILINE)
    assert readme_sets, "no `ssNN` bullets found under README's Stylistic sets heading"
    page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    page_sets = re.findall(r'<label><input type="checkbox" value="(ss\d+)">', page)
    assert page_sets == readme_sets
