# Dynamic Policy Config Forms Design

**Date:** 2026-02-04
**Status:** Implemented
**Last Updated:** 2026-02-05
**Commit:** 2cdfb94 (feat: add API-level validation and standardize get_config)
**Branch:** policy-config-ux
**PR:** #175

## Goal

Automatically generate rich configuration forms for policies based on their type definitions. Forms should:
- Render appropriate UI elements for each field type
- Support arbitrarily nested structures via recursive type definitions
- Handle discriminated unions (user picks type, form shows relevant fields)
- Manage dynamic lists and dicts (add/remove items)
- Work with any policy without policy-specific UI code

## Non-Goals

- Policy-specific form customizations
- Complex validation rules beyond what Pydantic supports
- Real-time collaborative editing

## Architecture Overview

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Pydantic       │────▶│  Policy          │────▶│  JSON Schema    │
│  Config Models  │     │  Discovery       │     │  + $defs        │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Pydantic       │◀────│  Admin API       │◀────│  Alpine.js      │
│  Validation     │     │  /policy/set     │     │  Form Renderer  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## Schema Layer: Pydantic Models

Policies define configuration using Pydantic models with rich metadata.

### Basic Types with Metadata

```python
from pydantic import BaseModel, Field

class JudgeConfig(BaseModel):
    model: str = Field(
        default="openai/gpt-4",
        description="LLM model to use for judging"
    )
    temperature: float = Field(
        default=0.0,
        ge=0,
        le=2,
        description="Sampling temperature"
    )
    api_key: str | None = Field(
        default=None,
        json_schema_extra={"format": "password"}
    )
```

### Discriminated Unions

For fields where the type determines which other fields are relevant:

```python
from typing import Annotated, Literal

class RegexRule(BaseModel):
    type: Literal["regex"] = "regex"
    pattern: str = Field(description="Regex pattern to match")
    case_sensitive: bool = False

class KeywordRule(BaseModel):
    type: Literal["keyword"] = "keyword"
    keywords: list[str] = Field(description="Keywords to detect")

# Discriminated union - UI renders dropdown for "type", shows relevant fields
RuleType = Annotated[RegexRule | KeywordRule, Field(discriminator="type")]
```

### Recursive Structures

For tree-like configurations:

```python
class Condition(BaseModel):
    type: Literal["and", "or", "not", "matches"]
    # Recursive: and/or/not contain child conditions
    conditions: list["Condition"] | None = None
    # Leaf: matches has a pattern
    pattern: str | None = None

# Pydantic handles forward references automatically
Condition.model_rebuild()
```

### Policy Usage

Policies use these models in their `__init__` signature:

```python
class MyPolicy(BasePolicy):
    def __init__(
        self,
        judge: JudgeConfig,
        rules: list[RuleType],
        enabled: bool = True,
    ):
        self.judge = judge
        self.rules = rules
        self.enabled = enabled
```

## Discovery Layer: Schema Extraction

Enhanced `policy_discovery.py` extracts JSON Schema from policy signatures.

### Process

1. **Inspect `__init__` signature** - Get parameter names and type hints
2. **Detect Pydantic models** - If type is `BaseModel` subclass, call `model_json_schema()`
3. **Handle unions** - `Annotated[A | B, Field(discriminator="type")]` produces `oneOf` with discriminator
4. **Fallback for primitives** - Use existing `python_type_to_json_schema()` for `str`, `int`, etc.

### Output Schema Structure

```json
{
  "type": "object",
  "properties": {
    "judge": {
      "$ref": "#/$defs/JudgeConfig"
    },
    "rules": {
      "type": "array",
      "items": {
        "oneOf": [
          {"$ref": "#/$defs/RegexRule"},
          {"$ref": "#/$defs/KeywordRule"}
        ],
        "discriminator": {
          "propertyName": "type",
          "mapping": {
            "regex": "#/$defs/RegexRule",
            "keyword": "#/$defs/KeywordRule"
          }
        }
      }
    },
    "enabled": {
      "type": "boolean",
      "default": true
    }
  },
  "$defs": {
    "JudgeConfig": {
      "type": "object",
      "properties": {
        "model": {"type": "string", "default": "openai/gpt-4", "description": "..."},
        "temperature": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.0},
        "api_key": {"type": "string", "format": "password", "nullable": true}
      }
    },
    "RegexRule": {
      "type": "object",
      "properties": {
        "type": {"const": "regex"},
        "pattern": {"type": "string", "description": "Regex pattern to match"},
        "case_sensitive": {"type": "boolean", "default": false}
      },
      "required": ["pattern"]
    },
    "KeywordRule": {
      "type": "object",
      "properties": {
        "type": {"const": "keyword"},
        "keywords": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["keywords"]
    }
  }
}
```

### Key Enhancements to Existing Discovery

- Extract schemas from Pydantic models (not just `__init__` signature primitives)
- Preserve `$defs` for nested type definitions
- Include `discriminator` metadata for union types
- Pass through `format`, `description`, `minimum`, `maximum`, etc.

## Frontend Layer: Alpine.js Dynamic Forms

### Technology Choice

**Alpine.js** - Lightweight reactive framework (~15kb) that enhances existing HTML. Fits well with current vanilla HTML templates, provides reactive state management needed for dynamic forms.

### Core Rendering Strategy

Recursive renderer walks JSON Schema and produces appropriate UI:

```javascript
function renderField(schema, path, value) {
  // Resolve $ref if present
  if (schema.$ref) {
    schema = resolveRef(schema.$ref);
  }

  // Discriminated union
  if (schema.oneOf && schema.discriminator) {
    return renderDiscriminatedUnion(schema, path, value);
  }

  // Basic types
  switch (schema.type) {
    case 'string':
      if (schema.format === 'password') return renderPasswordInput(schema, path, value);
      return renderTextInput(schema, path, value);
    case 'number':
    case 'integer':
      return renderNumberInput(schema, path, value);
    case 'boolean':
      return renderCheckbox(schema, path, value);
    case 'array':
      return renderArrayField(schema, path, value);
    case 'object':
      return renderObjectFields(schema, path, value);
  }
}
```

### Discriminated Unions

User selects variant type from dropdown, form shows relevant fields:

```html
<div x-data="{ item: { type: 'regex' } }">
  <label>Rule Type</label>
  <select x-model="item.type">
    <template x-for="variant in schema.oneOf">
      <option :value="variant.properties.type.const" x-text="variant.title || variant.properties.type.const"></option>
    </template>
  </select>

  <!-- Render fields for selected variant -->
  <template x-for="variant in schema.oneOf" :key="variant.properties.type.const">
    <div x-show="item.type === variant.properties.type.const">
      <!-- Recursive field rendering for this variant -->
    </div>
  </template>
</div>
```

### Dynamic Arrays

Add/remove items with inline or drawer rendering:

```html
<div class="array-field">
  <label x-text="schema.title || path"></label>

  <template x-for="(item, index) in getValue(path)" :key="index">
    <div class="array-item">
      <button @click="removeItem(path, index)" class="remove-btn">×</button>

      <!-- Simple items: render inline -->
      <template x-if="isSimpleType(schema.items)">
        <input x-model="getValue(path)[index]" />
      </template>

      <!-- Complex items: summary + edit button -->
      <template x-if="!isSimpleType(schema.items)">
        <div class="item-summary">
          <span x-text="summarize(item)"></span>
          <button @click="openDrawer(path, index)">Edit</button>
        </div>
      </template>
    </div>
  </template>

  <button @click="addItem(path, schema.items)" class="add-btn">+ Add</button>
</div>
```

### Drawer for Complex Items

Slide-out panel for editing nested objects without cluttering main form:

```html
<div x-show="drawerOpen" class="drawer">
  <div class="drawer-header">
    <h3 x-text="drawerTitle"></h3>
    <button @click="closeDrawer()">Done</button>
  </div>
  <div class="drawer-content">
    <!-- Recursive rendering of item fields -->
  </div>
</div>
```

### Complexity Heuristic

Determine if an item should render inline or in drawer:

```javascript
function isSimpleType(schema) {
  // Inline: primitives and arrays of primitives
  if (['string', 'number', 'integer', 'boolean'].includes(schema.type)) {
    return true;
  }
  if (schema.type === 'array' && isSimpleType(schema.items)) {
    return true;
  }
  // Drawer: objects, unions, nested arrays
  return false;
}
```

## Data Flow & Validation

### Form Submission

1. Alpine.js maintains form state as nested JavaScript object
2. On "Activate Policy", state serialized to JSON
3. POST to `/admin/policy/set` with `{ policy_class_ref, config }`
4. Server validates via Pydantic model
5. Success: policy activated, UI shows confirmation
6. Error: field-level errors returned, UI highlights problematic fields

### Error Response Format

```json
{
  "detail": [
    {
      "loc": ["rules", 0, "pattern"],
      "msg": "String should match pattern '^[a-z]+$'",
      "type": "string_pattern_mismatch"
    }
  ]
}
```

### Frontend Error Display

```javascript
function showValidationErrors(errors) {
  clearErrors();
  for (const error of errors) {
    const path = error.loc.join('.');
    const field = document.querySelector(`[data-path="${path}"]`);
    if (field) {
      field.classList.add('error');
      field.querySelector('.error-message').textContent = error.msg;
    }
  }
}
```

### Client-Side Validation (Progressive Enhancement)

- Required fields: warn if empty
- Number fields: respect min/max from schema
- Pattern fields: validate on blur
- Full validation remains server-side (Pydantic is source of truth)

## Migration Path

### Phase 1: Schema Infrastructure
- Add Pydantic model support to policy discovery
- Update discovery API to return full JSON Schema with `$defs`
- Existing policies continue working (primitives still supported)

### Phase 2: Form Renderer
- Add Alpine.js to policy config page
- Implement recursive schema renderer
- Support basic types, objects, arrays
- Replace current hardcoded type branching

### Phase 3: Advanced Features
- Discriminated union support
- Drawer for complex nested items
- Field-level validation feedback
- Password field masking

### Phase 4: Policy Migration
- Convert policies to use Pydantic config models
- Remove ParallelRulesPolicy (use new dynamic system instead)
- Add descriptions and constraints to existing policy configs

## File Changes

### New Files
- `src/luthien_proxy/static/alpine.min.js` - Alpine.js library
- `src/luthien_proxy/static/form_renderer.js` - Recursive form generation

### Modified Files
- `src/luthien_proxy/admin/policy_discovery.py` - Pydantic schema extraction
- `src/luthien_proxy/static/policy_config.html` - Alpine.js integration
- `src/luthien_proxy/static/policy_config.js` - Refactor to use new renderer

### Policy Files (Phase 4)
- Add Pydantic config models to policies that need rich forms
- Backward compatible: policies can still use primitives in `__init__`

## Testing Strategy

### Unit Tests
- Schema extraction from Pydantic models
- Discriminator handling for unions
- Recursive type resolution
- JSON Schema generation accuracy

### Integration Tests
- Form renders correctly for sample schemas
- Add/remove array items works
- Discriminated union selection updates form
- Validation errors display on correct fields

### Manual Testing
- Create policy with nested config, verify form usability
- Test keyboard navigation in dynamic forms
- Verify drawer works on mobile/narrow screens

---

## Implementation Notes (2026-02-05)

### What Was Built

All core features from the design were implemented:

1. **Schema Extraction** - `python_type_to_json_schema()` extracts full JSON Schema from Pydantic models including `$defs`, discriminators, and constraints
2. **Discriminated Unions** - Uses `TypeAdapter` for proper oneOf/discriminator schema generation
3. **Alpine.js Integration** - Reactive forms with two-way data binding via x-model
4. **Recursive Renderer** - `FormRenderer` handles nested objects, arrays, unions, and $ref resolution
5. **Validation Errors** - Server returns structured errors; UI highlights affected fields

### Additions Beyond Original Design

1. **API-Level Validation** - Added `validate_policy_config()` to validate configs before policy instantiation, catching Pydantic errors early with structured error responses

2. **Automatic `get_config()`** - `BasePolicy` now auto-generates `get_config()` by inspecting Pydantic model attributes, eliminating boilerplate in policies

3. **HTML Escaping** - `FormRenderer.escapeHtml()` prevents XSS in schema-derived content

4. **Dual Schema Format** - Renderer handles both standard JSON Schema (`{properties: {...}}`) and parameter-dict format (`{param1: {schema}, param2: {schema}}`)

5. **Nullable Type Handling** - Properly unwraps `anyOf: [Type, null]` patterns as nullable fields rather than unions

### Files Changed

```
src/luthien_proxy/admin/policy_discovery.py  - Schema extraction + validation
src/luthien_proxy/admin/routes.py            - API validation before enable
src/luthien_proxy/policy_core/base_policy.py - Auto get_config()
src/luthien_proxy/policies/sample_pydantic_policy.py - Example policy
src/luthien_proxy/static/vendor/alpine.min.js
src/luthien_proxy/static/form_renderer.js
src/luthien_proxy/static/policy_config.js
src/luthien_proxy/static/policy_config.html
tests/unit_tests/admin/test_policy_discovery.py
tests/unit_tests/test_admin_routes.py
```

### Not Implemented

- **Drawer for complex items** - Referenced in UI but not fully wired up; complex array items show JSON summary instead
- **Client-side validation** - Deferred to server-side Pydantic validation
- **Policy migration (Phase 4)** - Existing policies not yet converted to Pydantic configs
