# Dynamic Policy Config Forms Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automatically generate rich configuration forms from Pydantic models with support for nested objects, discriminated unions, and dynamic arrays.

**Architecture:** Pydantic models define config schemas with metadata (descriptions, constraints). Enhanced policy discovery extracts JSON Schema including `$defs` and discriminators. Alpine.js renders forms recursively with type-appropriate controls.

**Tech Stack:** Pydantic v2, Alpine.js 3.x, existing FastAPI/vanilla JS stack

---

## Task 1: Add Pydantic Schema Extraction to Policy Discovery

**Files:**
- Modify: `src/luthien_proxy/admin/policy_discovery.py:34-89`
- Test: `tests/unit_tests/admin/test_policy_discovery.py`

**Step 1: Write failing test for Pydantic model schema extraction**

Create test that verifies Pydantic models produce full JSON Schema:

```python
# tests/unit_tests/admin/test_policy_discovery.py

from pydantic import BaseModel, Field
from luthien_proxy.admin.policy_discovery import python_type_to_json_schema


class SampleConfig(BaseModel):
    """A sample config for testing."""
    name: str = Field(description="The name")
    temperature: float = Field(default=0.5, ge=0, le=2)
    api_key: str | None = Field(default=None, json_schema_extra={"format": "password"})


def test_pydantic_model_schema_extraction():
    """Pydantic models should produce full JSON Schema with constraints."""
    schema = python_type_to_json_schema(SampleConfig)

    assert schema["type"] == "object"
    assert "properties" in schema

    # Check name field has description
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["name"]["description"] == "The name"

    # Check temperature has constraints
    temp = schema["properties"]["temperature"]
    assert temp["type"] == "number"
    assert temp["minimum"] == 0
    assert temp["maximum"] == 2
    assert temp["default"] == 0.5

    # Check api_key has password format
    key = schema["properties"]["api_key"]
    assert key.get("format") == "password"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit_tests/admin/test_policy_discovery.py::test_pydantic_model_schema_extraction -v
```

Expected: FAIL - current implementation returns `{"type": "string"}` for unknown types.

**Step 3: Implement Pydantic detection in `python_type_to_json_schema()`**

Edit `src/luthien_proxy/admin/policy_discovery.py`, add at top of `python_type_to_json_schema()` (after line 34):

```python
def python_type_to_json_schema(python_type: Any) -> dict[str, Any]:
    """Convert a Python type hint to a JSON schema representation."""
    # Handle Pydantic models - extract full schema
    if isinstance(python_type, type):
        try:
            from pydantic import BaseModel
            if issubclass(python_type, BaseModel):
                return python_type.model_json_schema()
        except (ImportError, TypeError):
            pass

    # ... rest of existing function unchanged
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit_tests/admin/test_policy_discovery.py::test_pydantic_model_schema_extraction -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/luthien_proxy/admin/policy_discovery.py tests/unit_tests/admin/test_policy_discovery.py
git commit -m "feat(discovery): extract JSON Schema from Pydantic models"
```

---

## Task 2: Handle Discriminated Unions in Schema Extraction

**Files:**
- Modify: `src/luthien_proxy/admin/policy_discovery.py`
- Test: `tests/unit_tests/admin/test_policy_discovery.py`

**Step 1: Write failing test for discriminated union**

```python
# tests/unit_tests/admin/test_policy_discovery.py

from typing import Annotated, Literal
from pydantic import BaseModel, Field
from luthien_proxy.admin.policy_discovery import python_type_to_json_schema


class RegexRule(BaseModel):
    type: Literal["regex"] = "regex"
    pattern: str


class KeywordRule(BaseModel):
    type: Literal["keyword"] = "keyword"
    keywords: list[str]


RuleUnion = Annotated[RegexRule | KeywordRule, Field(discriminator="type")]


def test_discriminated_union_schema():
    """Discriminated unions should include oneOf with discriminator info."""
    schema = python_type_to_json_schema(RuleUnion)

    # Should have oneOf structure
    assert "oneOf" in schema or "anyOf" in schema
    variants = schema.get("oneOf") or schema.get("anyOf")
    assert len(variants) == 2

    # Should have discriminator metadata
    assert "discriminator" in schema
    assert schema["discriminator"]["propertyName"] == "type"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit_tests/admin/test_policy_discovery.py::test_discriminated_union_schema -v
```

Expected: FAIL - Annotated types not handled.

**Step 3: Add Annotated/Union handling**

Edit `python_type_to_json_schema()`, add after Pydantic model check:

```python
    # Handle Annotated types (may contain discriminated unions)
    origin = get_origin(python_type)
    if origin is Annotated:
        args = get_args(python_type)
        if args:
            base_type = args[0]
            # Check if it's a Union with Pydantic models
            base_origin = get_origin(base_type)
            if base_origin is Union:
                union_args = get_args(base_type)
                # Check if all args are Pydantic models
                try:
                    from pydantic import BaseModel
                    if all(isinstance(a, type) and issubclass(a, BaseModel) for a in union_args):
                        # Build discriminated union schema
                        from pydantic import TypeAdapter
                        adapter = TypeAdapter(python_type)
                        return adapter.json_schema()
                except (ImportError, TypeError):
                    pass
            # Fall through to handle the base type
            return python_type_to_json_schema(base_type)
```

Also add import at top of file:

```python
from typing import Annotated, get_origin, get_args, Union
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit_tests/admin/test_policy_discovery.py::test_discriminated_union_schema -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/luthien_proxy/admin/policy_discovery.py tests/unit_tests/admin/test_policy_discovery.py
git commit -m "feat(discovery): support discriminated unions via TypeAdapter"
```

---

## Task 3: Aggregate $defs from Policy Config Schema

**Files:**
- Modify: `src/luthien_proxy/admin/policy_discovery.py:92-141`
- Test: `tests/unit_tests/admin/test_policy_discovery.py`

**Step 1: Write failing test for $defs aggregation**

```python
# tests/unit_tests/admin/test_policy_discovery.py

from pydantic import BaseModel, Field
from luthien_proxy.admin.policy_discovery import extract_config_schema


class NestedConfig(BaseModel):
    value: int = 0


class ParentConfig(BaseModel):
    nested: NestedConfig
    name: str = "default"


class FakePolicy:
    """A fake policy for testing schema extraction."""
    def __init__(self, config: ParentConfig, enabled: bool = True):
        self.config = config
        self.enabled = enabled


def test_extract_config_schema_with_defs():
    """Config schema should include $defs for nested Pydantic models."""
    schema, example = extract_config_schema(FakePolicy)

    # Should have config parameter with nested structure
    assert "config" in schema
    config_schema = schema["config"]

    # Should have $defs at top level or within config schema
    assert "$defs" in config_schema or "definitions" in config_schema
    defs = config_schema.get("$defs") or config_schema.get("definitions", {})
    assert "NestedConfig" in defs
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit_tests/admin/test_policy_discovery.py::test_extract_config_schema_with_defs -v
```

Expected: FAIL - $defs not preserved currently.

**Step 3: Update extract_config_schema to preserve $defs**

The Pydantic `model_json_schema()` already includes `$defs`. Verify the current `python_type_to_json_schema` returns it. If not, ensure we're not stripping it:

```python
# In extract_config_schema(), the schema comes from python_type_to_json_schema()
# which now returns model_json_schema() for Pydantic models - $defs should be included
```

If test still fails, the issue is likely that $defs are at root level but we're only returning the properties. Check and fix accordingly.

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit_tests/admin/test_policy_discovery.py::test_extract_config_schema_with_defs -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/luthien_proxy/admin/policy_discovery.py tests/unit_tests/admin/test_policy_discovery.py
git commit -m "feat(discovery): preserve \$defs in extracted config schemas"
```

---

## Task 4: Add Alpine.js to Policy Config Page

**Files:**
- Create: `src/luthien_proxy/static/vendor/alpine.min.js`
- Modify: `src/luthien_proxy/static/policy_config.html:717`

**Step 1: Download Alpine.js**

```bash
curl -o src/luthien_proxy/static/vendor/alpine.min.js https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js
```

Note: Replace `3.x.x` with latest stable version (currently 3.14.3).

**Step 2: Add script tag to policy_config.html**

Edit `src/luthien_proxy/static/policy_config.html`, add before existing script tag (around line 717):

```html
    <script defer src="/static/vendor/alpine.min.js"></script>
    <script src="/static/policy_config.js"></script>
```

**Step 3: Verify page still loads**

Start the dev server and verify the policy config page loads without errors:

```bash
# In one terminal
./scripts/quick_start.sh

# In another, check the page loads
curl -s http://localhost:8000/policy-config | head -20
```

**Step 4: Commit**

```bash
git add src/luthien_proxy/static/vendor/alpine.min.js src/luthien_proxy/static/policy_config.html
git commit -m "feat(ui): add Alpine.js for reactive form rendering"
```

---

## Task 5: Create Recursive Form Renderer Module

**Files:**
- Create: `src/luthien_proxy/static/form_renderer.js`
- Test: Manual browser test

**Step 1: Create form_renderer.js with core structure**

```javascript
// src/luthien_proxy/static/form_renderer.js

/**
 * Recursive JSON Schema form renderer using Alpine.js
 */

const FormRenderer = {
  /**
   * Resolve $ref references in schema
   */
  resolveRef(schema, rootSchema) {
    if (!schema || !schema.$ref) return schema;
    const refPath = schema.$ref.replace('#/', '').split('/');
    let resolved = rootSchema;
    for (const part of refPath) {
      resolved = resolved[part];
    }
    return resolved;
  },

  /**
   * Check if schema represents a simple type (renders inline)
   */
  isSimpleType(schema) {
    if (!schema) return true;
    const type = schema.type;
    if (['string', 'number', 'integer', 'boolean'].includes(type)) {
      return true;
    }
    if (type === 'array' && schema.items) {
      return this.isSimpleType(schema.items);
    }
    return false;
  },

  /**
   * Get default value for a schema type
   */
  getDefaultValue(schema, rootSchema) {
    schema = this.resolveRef(schema, rootSchema);
    if (schema.default !== undefined) return schema.default;

    // Handle discriminated unions - pick first variant
    if (schema.oneOf || schema.anyOf) {
      const variants = schema.oneOf || schema.anyOf;
      const firstVariant = this.resolveRef(variants[0], rootSchema);
      return this.getDefaultValue(firstVariant, rootSchema);
    }

    switch (schema.type) {
      case 'string': return '';
      case 'number':
      case 'integer': return schema.minimum ?? 0;
      case 'boolean': return false;
      case 'array': return [];
      case 'object':
        const obj = {};
        if (schema.properties) {
          for (const [key, propSchema] of Object.entries(schema.properties)) {
            obj[key] = this.getDefaultValue(propSchema, rootSchema);
          }
        }
        return obj;
      default: return null;
    }
  },

  /**
   * Render a field based on its schema
   * Returns HTML string with Alpine.js bindings
   */
  renderField(schema, path, rootSchema, depth = 0) {
    schema = this.resolveRef(schema, rootSchema);

    // Discriminated union
    if (schema.oneOf || schema.anyOf) {
      return this.renderUnionField(schema, path, rootSchema, depth);
    }

    switch (schema.type) {
      case 'string':
        return this.renderStringField(schema, path);
      case 'number':
      case 'integer':
        return this.renderNumberField(schema, path, schema.type);
      case 'boolean':
        return this.renderBooleanField(schema, path);
      case 'array':
        return this.renderArrayField(schema, path, rootSchema, depth);
      case 'object':
        return this.renderObjectField(schema, path, rootSchema, depth);
      default:
        return this.renderStringField(schema, path); // fallback
    }
  },

  renderStringField(schema, path) {
    const inputType = schema.format === 'password' ? 'password' : 'text';
    const desc = schema.description ? `<span class="field-hint">${schema.description}</span>` : '';
    return `
      <div class="form-field">
        <label>${this.pathToLabel(path)}</label>
        <input type="${inputType}" x-model="formData.${path}"
               ${schema.pattern ? `pattern="${schema.pattern}"` : ''}>
        ${desc}
      </div>
    `;
  },

  renderNumberField(schema, path, type) {
    const step = type === 'integer' ? '1' : 'any';
    const min = schema.minimum !== undefined ? `min="${schema.minimum}"` : '';
    const max = schema.maximum !== undefined ? `max="${schema.maximum}"` : '';
    const desc = schema.description ? `<span class="field-hint">${schema.description}</span>` : '';
    return `
      <div class="form-field">
        <label>${this.pathToLabel(path)}</label>
        <input type="number" x-model.number="formData.${path}" step="${step}" ${min} ${max}>
        ${desc}
      </div>
    `;
  },

  renderBooleanField(schema, path) {
    const desc = schema.description ? `<span class="field-hint">${schema.description}</span>` : '';
    return `
      <div class="form-field form-field-checkbox">
        <label>
          <input type="checkbox" x-model="formData.${path}">
          ${this.pathToLabel(path)}
        </label>
        ${desc}
      </div>
    `;
  },

  renderArrayField(schema, path, rootSchema, depth) {
    const itemSchema = this.resolveRef(schema.items, rootSchema);
    const isSimple = this.isSimpleType(itemSchema);
    const desc = schema.description ? `<span class="field-hint">${schema.description}</span>` : '';

    if (isSimple) {
      // Inline array of simple items
      return `
        <div class="form-field form-field-array">
          <label>${this.pathToLabel(path)}</label>
          ${desc}
          <template x-for="(item, index) in formData.${path}" :key="index">
            <div class="array-item">
              <input type="text" x-model="formData.${path}[index]">
              <button type="button" class="btn-remove" @click="formData.${path}.splice(index, 1)">&times;</button>
            </div>
          </template>
          <button type="button" class="btn-add" @click="formData.${path}.push('')">+ Add</button>
        </div>
      `;
    } else {
      // Complex items - show summary with edit
      return `
        <div class="form-field form-field-array">
          <label>${this.pathToLabel(path)}</label>
          ${desc}
          <template x-for="(item, index) in formData.${path}" :key="index">
            <div class="array-item array-item-complex">
              <span class="item-summary" x-text="JSON.stringify(item).slice(0, 50) + '...'"></span>
              <button type="button" class="btn-edit" @click="openDrawer('${path}', index)">Edit</button>
              <button type="button" class="btn-remove" @click="formData.${path}.splice(index, 1)">&times;</button>
            </div>
          </template>
          <button type="button" class="btn-add" @click="addArrayItem('${path}')">+ Add</button>
        </div>
      `;
    }
  },

  renderObjectField(schema, path, rootSchema, depth) {
    if (!schema.properties) {
      // Freeform object - render as JSON textarea
      return `
        <div class="form-field">
          <label>${this.pathToLabel(path)}</label>
          <textarea x-model="formData.${path}" @input="validateJson($event, '${path}')"></textarea>
        </div>
      `;
    }

    // Render each property
    let html = `<fieldset class="form-fieldset depth-${depth}"><legend>${this.pathToLabel(path)}</legend>`;
    for (const [key, propSchema] of Object.entries(schema.properties)) {
      const propPath = path ? `${path}.${key}` : key;
      html += this.renderField(propSchema, propPath, rootSchema, depth + 1);
    }
    html += '</fieldset>';
    return html;
  },

  renderUnionField(schema, path, rootSchema, depth) {
    const variants = schema.oneOf || schema.anyOf;
    const discriminator = schema.discriminator?.propertyName || 'type';
    const desc = schema.description ? `<span class="field-hint">${schema.description}</span>` : '';

    // Build options from variants
    let options = '';
    for (const variant of variants) {
      const resolved = this.resolveRef(variant, rootSchema);
      const typeValue = resolved.properties?.[discriminator]?.const ||
                        resolved.properties?.[discriminator]?.default ||
                        resolved.title || 'unknown';
      options += `<option value="${typeValue}">${typeValue}</option>`;
    }

    return `
      <div class="form-field form-field-union">
        <label>${this.pathToLabel(path)} Type</label>
        ${desc}
        <select x-model="formData.${path}.${discriminator}" @change="onUnionTypeChange('${path}', $event.target.value)">
          ${options}
        </select>
        <div class="union-fields">
          ${variants.map((variant, i) => {
            const resolved = this.resolveRef(variant, rootSchema);
            const typeValue = resolved.properties?.[discriminator]?.const ||
                              resolved.properties?.[discriminator]?.default || i;
            return `
              <template x-if="formData.${path}.${discriminator} === '${typeValue}'">
                <div>${this.renderObjectField(resolved, path, rootSchema, depth + 1)}</div>
              </template>
            `;
          }).join('')}
        </div>
      </div>
    `;
  },

  pathToLabel(path) {
    if (!path) return '';
    const parts = path.split('.');
    const last = parts[parts.length - 1];
    // Convert snake_case/camelCase to Title Case
    return last
      .replace(/_/g, ' ')
      .replace(/([A-Z])/g, ' $1')
      .replace(/^./, s => s.toUpperCase())
      .trim();
  },

  /**
   * Generate full form HTML for a schema
   */
  generateForm(schema, rootSchema = null) {
    rootSchema = rootSchema || schema;
    let html = '';

    if (schema.properties) {
      for (const [key, propSchema] of Object.entries(schema.properties)) {
        html += this.renderField(propSchema, key, rootSchema, 0);
      }
    }

    return html;
  }
};

// Export for use in policy_config.js
window.FormRenderer = FormRenderer;
```

**Step 2: Add script include to policy_config.html**

Edit `src/luthien_proxy/static/policy_config.html`, add before policy_config.js:

```html
    <script defer src="/static/vendor/alpine.min.js"></script>
    <script src="/static/form_renderer.js"></script>
    <script src="/static/policy_config.js"></script>
```

**Step 3: Manual test - verify no JS errors**

Open browser console on policy config page, verify no errors.

**Step 4: Commit**

```bash
git add src/luthien_proxy/static/form_renderer.js src/luthien_proxy/static/policy_config.html
git commit -m "feat(ui): add recursive form renderer module"
```

---

## Task 6: Integrate Form Renderer into Policy Config Page

**Files:**
- Modify: `src/luthien_proxy/static/policy_config.js:306-413`
- Modify: `src/luthien_proxy/static/policy_config.html`

**Step 1: Add Alpine.js data structure to page**

Edit `policy_config.html`, wrap the form container with Alpine data:

```html
<div class="config-section" x-data="policyConfigForm()" x-init="init()">
  <h3>Configuration</h3>
  <div class="config-form" id="config-form" x-html="formHtml"></div>
  <!-- ... rest of section -->
</div>
```

**Step 2: Update policy_config.js to use FormRenderer**

Replace the `renderConfigForm` function body (lines 306-413) with:

```javascript
function renderConfigForm(policy) {
  const container = document.getElementById('config-form');
  if (!container) return;

  const schema = policy.config_schema || {};
  const example = policy.example_config || {};

  // Check if schema has Pydantic structure (properties at root or $defs)
  const hasPydanticSchema = schema.properties || schema.$defs;

  if (hasPydanticSchema && window.FormRenderer) {
    // Use new recursive renderer
    renderWithAlpine(container, schema, example);
  } else {
    // Fallback to legacy rendering for non-Pydantic configs
    renderLegacyForm(container, schema, example);
  }
}

function renderWithAlpine(container, schema, initialData) {
  // Initialize form data with defaults merged with example
  const formData = FormRenderer.getDefaultValue(schema, schema);
  Object.assign(formData, initialData);

  // Store in state for submission
  state.currentFormData = formData;

  // Generate form HTML
  const formHtml = FormRenderer.generateForm(schema);

  // Create Alpine component
  container.innerHTML = `
    <div x-data="{ formData: ${JSON.stringify(formData)} }"
         x-init="$watch('formData', value => window.updateFormData(value))">
      ${formHtml}
    </div>
  `;

  // Re-init Alpine on new content
  if (window.Alpine) {
    Alpine.initTree(container);
  }
}

// Global callback for Alpine data binding
window.updateFormData = function(data) {
  state.currentFormData = data;
  updateActivateButton();
};

function renderLegacyForm(container, schema, example) {
  // ... move existing rendering code here as fallback
  container.innerHTML = '';
  // ... rest of current implementation
}
```

**Step 3: Update getConfigValues to use Alpine state**

```javascript
function getConfigValues() {
  // If using Alpine form, return the reactive data
  if (state.currentFormData) {
    return state.currentFormData;
  }
  // Otherwise use legacy form value extraction
  // ... existing code
}
```

**Step 4: Test with existing policies**

Verify existing policies still render correctly (they'll use legacy renderer).

**Step 5: Commit**

```bash
git add src/luthien_proxy/static/policy_config.js src/luthien_proxy/static/policy_config.html
git commit -m "feat(ui): integrate FormRenderer with fallback for legacy schemas"
```

---

## Task 7: Add CSS for Dynamic Form Elements

**Files:**
- Modify: `src/luthien_proxy/static/policy_config.html` (style section)

**Step 1: Add styles for new form elements**

Add to the `<style>` section in policy_config.html:

```css
/* Dynamic form styles */
.form-fieldset {
  border: 1px solid var(--border-color, #333);
  border-radius: 4px;
  padding: 1rem;
  margin: 0.5rem 0;
}

.form-fieldset legend {
  font-weight: 500;
  padding: 0 0.5rem;
}

.form-fieldset.depth-1 {
  background: rgba(255, 255, 255, 0.02);
}

.form-fieldset.depth-2 {
  background: rgba(255, 255, 255, 0.04);
}

.form-field-array .array-item {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.5rem;
}

.form-field-array .array-item input {
  flex: 1;
}

.form-field-array .array-item-complex {
  background: var(--input-bg, #1a1a1a);
  padding: 0.5rem;
  border-radius: 4px;
}

.form-field-array .item-summary {
  flex: 1;
  font-family: monospace;
  font-size: 0.875rem;
  color: var(--text-secondary, #888);
}

.btn-remove {
  background: var(--error-color, #ff4444);
  color: white;
  border: none;
  border-radius: 4px;
  width: 24px;
  height: 24px;
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
}

.btn-add {
  background: transparent;
  border: 1px dashed var(--border-color, #333);
  color: var(--text-secondary, #888);
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
  width: 100%;
  margin-top: 0.5rem;
}

.btn-add:hover {
  border-color: var(--accent-color, #4a9eff);
  color: var(--accent-color, #4a9eff);
}

.btn-edit {
  background: var(--accent-color, #4a9eff);
  color: white;
  border: none;
  border-radius: 4px;
  padding: 0.25rem 0.5rem;
  cursor: pointer;
  font-size: 0.75rem;
}

.form-field-union select {
  margin-bottom: 0.5rem;
}

.union-fields {
  padding-left: 1rem;
  border-left: 2px solid var(--border-color, #333);
}

.field-hint {
  display: block;
  font-size: 0.75rem;
  color: var(--text-secondary, #888);
  margin-top: 0.25rem;
}

.form-field-checkbox label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}

.form-field-checkbox input[type="checkbox"] {
  width: auto;
}
```

**Step 2: Commit**

```bash
git add src/luthien_proxy/static/policy_config.html
git commit -m "style(ui): add CSS for dynamic form elements"
```

---

## Task 8: Add Drawer Component for Complex Nested Items

**Files:**
- Modify: `src/luthien_proxy/static/form_renderer.js`
- Modify: `src/luthien_proxy/static/policy_config.html`

**Step 1: Add drawer HTML structure to page**

Add at end of body in policy_config.html:

```html
<!-- Drawer for editing complex items -->
<div id="form-drawer" class="drawer" x-data="{ open: false, path: '', index: -1, schema: null }"
     x-show="open" x-cloak>
  <div class="drawer-backdrop" @click="open = false"></div>
  <div class="drawer-content">
    <div class="drawer-header">
      <h3 x-text="'Edit ' + path"></h3>
      <button @click="open = false" class="drawer-close">&times;</button>
    </div>
    <div class="drawer-body" id="drawer-form"></div>
    <div class="drawer-footer">
      <button @click="open = false" class="btn-primary">Done</button>
    </div>
  </div>
</div>
```

**Step 2: Add drawer styles**

```css
.drawer {
  position: fixed;
  inset: 0;
  z-index: 1000;
}

.drawer-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
}

.drawer-content {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 400px;
  max-width: 90vw;
  background: var(--bg-primary, #0a0a0a);
  display: flex;
  flex-direction: column;
  box-shadow: -4px 0 20px rgba(0, 0, 0, 0.3);
}

.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem;
  border-bottom: 1px solid var(--border-color, #333);
}

.drawer-close {
  background: none;
  border: none;
  color: var(--text-primary, #fff);
  font-size: 1.5rem;
  cursor: pointer;
}

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.drawer-footer {
  padding: 1rem;
  border-top: 1px solid var(--border-color, #333);
}

[x-cloak] {
  display: none !important;
}
```

**Step 3: Add drawer open/close functions to form_renderer.js**

```javascript
// Add to FormRenderer object
openDrawer(path, index) {
  const drawer = document.getElementById('form-drawer');
  if (!drawer || !window.Alpine) return;

  // Get the item schema from the array schema
  // This requires storing schema references - simplify for now
  Alpine.store('drawer', { open: true, path, index });
},

closeDrawer() {
  if (window.Alpine) {
    Alpine.store('drawer', { open: false, path: '', index: -1 });
  }
}

// Make functions global for Alpine access
window.openDrawer = FormRenderer.openDrawer.bind(FormRenderer);
window.closeDrawer = FormRenderer.closeDrawer.bind(FormRenderer);
```

**Step 4: Commit**

```bash
git add src/luthien_proxy/static/form_renderer.js src/luthien_proxy/static/policy_config.html
git commit -m "feat(ui): add drawer component for editing complex items"
```

---

## Task 9: Create Sample Pydantic-Based Policy for Testing

**Files:**
- Create: `src/luthien_proxy/policies/sample_pydantic_policy.py`
- Test: `tests/unit_tests/policies/test_sample_pydantic_policy.py`

**Step 1: Write test for sample policy**

```python
# tests/unit_tests/policies/test_sample_pydantic_policy.py

import pytest
from luthien_proxy.policies.sample_pydantic_policy import (
    SamplePydanticPolicy,
    SampleConfig,
    RuleConfig,
)
from luthien_proxy.admin.policy_discovery import extract_config_schema


def test_sample_policy_accepts_pydantic_config():
    """Policy should accept Pydantic model as config."""
    config = SampleConfig(
        name="test",
        rules=[RuleConfig(type="keyword", keywords=["bad", "word"])],
    )
    policy = SamplePydanticPolicy(config=config)
    assert policy.config.name == "test"


def test_sample_policy_schema_extraction():
    """Policy schema should include full Pydantic structure."""
    schema, example = extract_config_schema(SamplePydanticPolicy)

    assert "config" in schema
    config_schema = schema["config"]

    # Should have $defs for nested types
    assert "$defs" in config_schema
    assert "RuleConfig" in config_schema["$defs"] or any(
        "RuleConfig" in str(d) for d in config_schema.get("$defs", {}).values()
    )
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit_tests/policies/test_sample_pydantic_policy.py -v
```

Expected: FAIL - module doesn't exist.

**Step 3: Create sample policy**

```python
# src/luthien_proxy/policies/sample_pydantic_policy.py

"""Sample policy demonstrating Pydantic config models for dynamic form generation."""

from typing import Annotated, Literal
from pydantic import BaseModel, Field

from luthien_proxy.policies.base_policy import BasePolicy
from luthien_proxy.policy_core.contexts import RequestContext, StreamContext
from luthien_proxy.policy_core.chunk_builders import ChunkBuilder


class RegexRuleConfig(BaseModel):
    """Rule that matches content against a regex pattern."""

    type: Literal["regex"] = "regex"
    pattern: str = Field(description="Regular expression pattern to match")
    case_sensitive: bool = Field(default=False, description="Whether matching is case-sensitive")


class KeywordRuleConfig(BaseModel):
    """Rule that matches content against a list of keywords."""

    type: Literal["keyword"] = "keyword"
    keywords: list[str] = Field(description="Keywords to detect in content")


RuleConfig = Annotated[
    RegexRuleConfig | KeywordRuleConfig, Field(discriminator="type")
]


class SampleConfig(BaseModel):
    """Configuration for the sample policy."""

    name: str = Field(default="default", description="Name for this policy instance")
    enabled: bool = Field(default=True, description="Whether the policy is active")
    threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Detection threshold (0-1)"
    )
    api_key: str | None = Field(
        default=None, json_schema_extra={"format": "password"}
    )
    rules: list[RuleConfig] = Field(
        default_factory=list, description="List of detection rules"
    )


class SamplePydanticPolicy(BasePolicy):
    """
    Sample policy demonstrating Pydantic-based configuration.

    This policy does nothing but serves as an example for the dynamic
    form generation system. It shows:
    - Basic types with constraints (threshold with min/max)
    - Password fields (api_key)
    - Discriminated unions (rules with type selector)
    - Nested objects and arrays
    """

    def __init__(self, config: SampleConfig | None = None):
        self.config = config or SampleConfig()

    async def on_request(
        self, context: RequestContext, chunk_builder: ChunkBuilder
    ) -> RequestContext:
        return context

    async def on_stream_chunk(
        self, context: StreamContext, chunk_builder: ChunkBuilder
    ) -> StreamContext:
        return context

    def get_config(self) -> dict:
        return {"config": self.config.model_dump()}
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit_tests/policies/test_sample_pydantic_policy.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/luthien_proxy/policies/sample_pydantic_policy.py tests/unit_tests/policies/test_sample_pydantic_policy.py
git commit -m "feat(policies): add sample Pydantic policy for testing dynamic forms"
```

---

## Task 10: Add Server-Side Validation Error Response

**Files:**
- Modify: `src/luthien_proxy/admin/routes.py:150-188`
- Test: `tests/unit_tests/admin/test_routes.py`

**Step 1: Write test for validation error format**

```python
# tests/unit_tests/admin/test_routes.py (add to existing)

from pydantic import ValidationError


def test_policy_set_returns_validation_errors():
    """Setting a policy with invalid config should return field-level errors."""
    # This test verifies the error response format
    # Actual implementation depends on policy having Pydantic validation
    pass  # Placeholder - implement when endpoint is updated
```

**Step 2: Update set_policy to catch Pydantic ValidationError**

Edit `src/luthien_proxy/admin/routes.py`, in the `set_policy` function, wrap the policy instantiation:

```python
from pydantic import ValidationError

@router.post("/policy/set", response_model=PolicyEnableResponse)
async def set_policy(request: PolicySetRequest):
    # ... existing code to load class ...

    try:
        result = await policy_manager.set_policy(
            policy_class, request.config, request.enabled_by
        )
    except ValidationError as e:
        # Return Pydantic validation errors in structured format
        return PolicyEnableResponse(
            success=False,
            error="Validation error",
            troubleshooting=[
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()
            ],
            validation_errors=e.errors(),  # Add this field to response model
        )
    # ... rest of function
```

**Step 3: Add validation_errors field to PolicyEnableResponse**

```python
class PolicyEnableResponse(BaseModel):
    success: bool
    message: str | None = None
    policy_name: str | None = None
    error: str | None = None
    troubleshooting: list[str] | None = None
    validation_errors: list[dict] | None = None  # New field
```

**Step 4: Commit**

```bash
git add src/luthien_proxy/admin/routes.py
git commit -m "feat(admin): return structured validation errors from policy/set"
```

---

## Task 11: Display Validation Errors in Form UI

**Files:**
- Modify: `src/luthien_proxy/static/policy_config.js`

**Step 1: Update error handling in activatePolicy**

In `policy_config.js`, update the `activatePolicy` function to handle validation errors:

```javascript
async function activatePolicy() {
  // ... existing code ...

  try {
    const response = await fetch('/admin/policy/set', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': adminKey },
      body: JSON.stringify(payload),
    });

    const data = await response.json();

    if (data.success) {
      showStatus('Policy activated successfully', 'success');
    } else {
      // Handle validation errors
      if (data.validation_errors && data.validation_errors.length > 0) {
        highlightValidationErrors(data.validation_errors);
        showStatus('Validation errors - check highlighted fields', 'error');
      } else {
        showStatus(data.error || 'Failed to activate policy', 'error');
      }
    }
  } catch (err) {
    showStatus('Error: ' + err.message, 'error');
  }
}

function highlightValidationErrors(errors) {
  // Clear previous errors
  document.querySelectorAll('.field-error').forEach(el => el.remove());
  document.querySelectorAll('.form-field.has-error').forEach(el => {
    el.classList.remove('has-error');
  });

  for (const error of errors) {
    const path = error.loc.join('.');
    // Find field by path (data attribute or name)
    const field = document.querySelector(`[data-path="${path}"], [name="${path}"]`);
    if (field) {
      const container = field.closest('.form-field');
      if (container) {
        container.classList.add('has-error');
        const errorEl = document.createElement('span');
        errorEl.className = 'field-error';
        errorEl.textContent = error.msg;
        container.appendChild(errorEl);
      }
    }
  }
}
```

**Step 2: Add error styles**

```css
.form-field.has-error input,
.form-field.has-error textarea,
.form-field.has-error select {
  border-color: var(--error-color, #ff4444);
}

.field-error {
  display: block;
  color: var(--error-color, #ff4444);
  font-size: 0.75rem;
  margin-top: 0.25rem;
}
```

**Step 3: Commit**

```bash
git add src/luthien_proxy/static/policy_config.js src/luthien_proxy/static/policy_config.html
git commit -m "feat(ui): display server-side validation errors on form fields"
```

---

## Task 12: End-to-End Test with Sample Policy

**Files:**
- Test: Manual browser testing

**Step 1: Start the dev server**

```bash
./scripts/quick_start.sh
```

**Step 2: Navigate to policy config page**

Open `http://localhost:8000/policy-config` in browser.

**Step 3: Select SamplePydanticPolicy from dropdown**

Verify:
- Form renders with structured fields (not JSON textarea)
- `name` shows as text input with description hint
- `threshold` shows as number input with 0-1 constraints
- `api_key` shows as password input
- `rules` shows as array with add/remove buttons
- Rule type dropdown shows "regex" and "keyword" options
- Selecting rule type shows appropriate fields

**Step 4: Test validation**

- Set threshold to 2.0 (invalid)
- Click Activate
- Verify error highlights threshold field

**Step 5: Test successful activation**

- Fix threshold to valid value
- Add a keyword rule
- Click Activate
- Verify success message

**Step 6: Document any issues found**

Create issues or fix inline as needed.

---

## Task 13: Run Full Test Suite and Format

**Step 1: Format code**

```bash
./scripts/format_all.sh
```

**Step 2: Run dev checks**

```bash
./scripts/dev_checks.sh
```

**Step 3: Fix any issues**

Address linting, type errors, or test failures.

**Step 4: Final commit if changes needed**

```bash
git add -A
git commit -m "chore: fix linting and type errors"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Pydantic schema extraction | policy_discovery.py |
| 2 | Discriminated union support | policy_discovery.py |
| 3 | $defs aggregation | policy_discovery.py |
| 4 | Add Alpine.js | vendor/alpine.min.js, policy_config.html |
| 5 | Create form renderer module | form_renderer.js |
| 6 | Integrate with policy config | policy_config.js, policy_config.html |
| 7 | Add form CSS | policy_config.html |
| 8 | Add drawer component | form_renderer.js, policy_config.html |
| 9 | Sample Pydantic policy | sample_pydantic_policy.py |
| 10 | Server-side validation errors | routes.py |
| 11 | Display validation errors | policy_config.js |
| 12 | End-to-end testing | Manual |
| 13 | Format and test suite | Scripts |

Total estimated commits: 12-15
