from pathlib import Path
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parent.parent


def test_mono_preview_resets_to_regular_weight():
    script = textwrap.dedent(
        """
        import assert from 'node:assert/strict';
        import { initToggles } from './test/shared.js';

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

        weightToggle.click();
        assert.equal(style.get('--font-weight'), '600');
        assert.equal(toggles.getState().weightIndex, 2);

        fontToggle.click();
        assert.equal(style.get('--font-weight'), '400');
        assert.equal(toggles.getState().weightIndex, 2);

        fontToggle.click();
        assert.equal(style.get('--font-weight'), '600');
        """
    )

    subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        cwd=ROOT,
    )
