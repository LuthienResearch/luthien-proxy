/**
 * Recursive JSON Schema form renderer using Alpine.js
 *
 * Generates HTML forms with Alpine.js bindings from JSON Schema definitions.
 * Supports nested objects, arrays, discriminated unions, and $ref resolution.
 */

const FormRenderer = {
  /**
   * Resolve $ref references in schema
   */
  resolveRef(schema, rootSchema) {
    if (!schema || !schema.$ref) return schema;
    const refPath = schema.$ref.replace("#/", "").split("/");
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
    if (["string", "number", "integer", "boolean"].includes(type)) {
      return true;
    }
    if (type === "array" && schema.items) {
      return this.isSimpleType(schema.items);
    }
    return false;
  },

  /**
   * Get default value for a schema type
   * Handles both standard JSON Schema and parameter schema dicts
   */
  getDefaultValue(schema, rootSchema) {
    schema = this.resolveRef(schema, rootSchema);
    // Use default value unless it's null and schema is an object with properties
    // In that case, we want to construct the object with property defaults
    if (schema.default !== undefined && schema.default !== null) {
      return schema.default;
    }
    if (schema.default === null && schema.type !== "object") {
      return null;
    }

    // Handle discriminated unions - pick first variant
    if (schema.oneOf || schema.anyOf) {
      const variants = schema.oneOf || schema.anyOf;
      const firstVariant = this.resolveRef(variants[0], rootSchema);
      return this.getDefaultValue(firstVariant, rootSchema);
    }

    // If no type but has nested schemas (parameter dict format), build object
    if (!schema.type && !schema.properties) {
      const hasNestedSchemas = Object.values(schema).some(
        (v) => v && typeof v === "object" && (v.type || v.properties || v.$defs)
      );
      if (hasNestedSchemas) {
        const obj = {};
        for (const [key, paramSchema] of Object.entries(schema)) {
          if (paramSchema && typeof paramSchema === "object") {
            const paramRoot = paramSchema.$defs ? paramSchema : rootSchema;
            obj[key] = this.getDefaultValue(paramSchema, paramRoot);
          }
        }
        return obj;
      }
    }

    switch (schema.type) {
      case "string":
        return "";
      case "number":
      case "integer":
        return schema.minimum ?? 0;
      case "boolean":
        return false;
      case "array":
        return [];
      case "object":
        const obj = {};
        if (schema.properties) {
          for (const [key, propSchema] of Object.entries(schema.properties)) {
            obj[key] = this.getDefaultValue(propSchema, rootSchema);
          }
        }
        return obj;
      default:
        return null;
    }
  },

  /**
   * Render a field based on its schema
   * Returns HTML string with Alpine.js bindings
   */
  renderField(schema, path, rootSchema, depth = 0) {
    schema = this.resolveRef(schema, rootSchema);

    // Handle anyOf with [type, null] as nullable - not a union
    if (schema.anyOf) {
      const nonNullTypes = schema.anyOf.filter(
        (s) => s.type !== "null" && s !== null
      );
      if (nonNullTypes.length === 1) {
        // This is a nullable type, merge the non-null schema
        const mergedSchema = { ...schema, ...nonNullTypes[0] };
        delete mergedSchema.anyOf;
        return this.renderField(mergedSchema, path, rootSchema, depth);
      }
    }

    // Discriminated union (oneOf or anyOf with multiple non-null types)
    if (schema.oneOf || schema.anyOf) {
      return this.renderUnionField(schema, path, rootSchema, depth);
    }

    switch (schema.type) {
      case "string":
        return this.renderStringField(schema, path);
      case "number":
      case "integer":
        return this.renderNumberField(schema, path, schema.type);
      case "boolean":
        return this.renderBooleanField(schema, path);
      case "array":
        return this.renderArrayField(schema, path, rootSchema, depth);
      case "object":
        return this.renderObjectField(schema, path, rootSchema, depth);
      default:
        return this.renderStringField(schema, path); // fallback
    }
  },

  renderStringField(schema, path) {
    const inputType = schema.format === "password" ? "password" : "text";
    const desc = schema.description
      ? `<span class="field-hint">${this.escapeHtml(schema.description)}</span>`
      : "";
    const pattern = schema.pattern
      ? `pattern="${this.escapeHtml(schema.pattern)}"`
      : "";
    return `
      <div class="form-field">
        <label>${this.escapeHtml(this.pathToLabel(path))}</label>
        <input type="${inputType}" x-model="formData.${path}" ${pattern}>
        ${desc}
      </div>
    `;
  },

  renderNumberField(schema, path, type) {
    const step = type === "integer" ? "1" : "any";
    const min =
      schema.minimum !== undefined ? `min="${schema.minimum}"` : "";
    const max =
      schema.maximum !== undefined ? `max="${schema.maximum}"` : "";
    const desc = schema.description
      ? `<span class="field-hint">${this.escapeHtml(schema.description)}</span>`
      : "";
    return `
      <div class="form-field">
        <label>${this.escapeHtml(this.pathToLabel(path))}</label>
        <input type="number" x-model.number="formData.${path}" step="${step}" ${min} ${max}>
        ${desc}
      </div>
    `;
  },

  renderBooleanField(schema, path) {
    const desc = schema.description
      ? `<span class="field-hint">${this.escapeHtml(schema.description)}</span>`
      : "";
    return `
      <div class="form-field form-field-checkbox">
        <label>
          <input type="checkbox" x-model="formData.${path}">
          ${this.escapeHtml(this.pathToLabel(path))}
        </label>
        ${desc}
      </div>
    `;
  },

  renderArrayField(schema, path, rootSchema, depth) {
    const itemSchema = this.resolveRef(schema.items, rootSchema);
    const isSimple = this.isSimpleType(itemSchema);
    const desc = schema.description
      ? `<span class="field-hint">${this.escapeHtml(schema.description)}</span>`
      : "";

    if (isSimple) {
      const isNumeric = itemSchema && (itemSchema.type === "number" || itemSchema.type === "integer");
      const inputType = isNumeric ? "number" : "text";
      const modelModifier = isNumeric ? ".number" : "";
      const defaultValue = isNumeric ? "0" : "''";
      const step = itemSchema?.type === "integer" ? ' step="1"' : "";
      return `
        <div class="form-field form-field-array">
          <label>${this.escapeHtml(this.pathToLabel(path))}</label>
          ${desc}
          <template x-for="(item, index) in formData.${path}" :key="index">
            <div class="array-item">
              <input type="${inputType}" x-model${modelModifier}="formData.${path}[index]"${step}>
              <button type="button" class="btn-remove" @click="formData.${path}.splice(index, 1)">&times;</button>
            </div>
          </template>
          <button type="button" class="btn-add" @click="formData.${path}.push(${defaultValue})">+ Add</button>
        </div>
      `;
    } else {
      // Complex items - show summary with edit
      return `
        <div class="form-field form-field-array">
          <label>${this.escapeHtml(this.pathToLabel(path))}</label>
          ${desc}
          <template x-for="(item, index) in formData.${path}" :key="index">
            <div class="array-item array-item-complex">
              <span class="item-summary" x-text="JSON.stringify(item).slice(0, 50) + '...'"></span>
              <button type="button" class="btn-edit" @click="openDrawer('${path}', index)">Edit</button>
              <button type="button" class="btn-remove" @click="formData.${path}.splice(index, 1)">&times;</button>
            </div>
          </template>
          <!-- Add button hidden until drawer supports creating new items -->
        </div>
      `;
    }
  },

  renderObjectField(schema, path, rootSchema, depth) {
    if (!schema.properties) {
      // Freeform object - render as JSON textarea
      return `
        <div class="form-field">
          <label>${this.escapeHtml(this.pathToLabel(path))}</label>
          <textarea x-model="formData.${path}" @input="validateJson($event, '${path}')"></textarea>
        </div>
      `;
    }

    // Render each property
    let html = `<fieldset class="form-fieldset depth-${depth}"><legend>${this.escapeHtml(this.pathToLabel(path))}</legend>`;
    for (const [key, propSchema] of Object.entries(schema.properties)) {
      const propPath = path ? `${path}.${key}` : key;
      html += this.renderField(propSchema, propPath, rootSchema, depth + 1);
    }
    html += "</fieldset>";
    return html;
  },

  renderUnionField(schema, path, rootSchema, depth) {
    const variants = schema.oneOf || schema.anyOf;
    const discriminator = schema.discriminator?.propertyName || "type";
    const desc = schema.description
      ? `<span class="field-hint">${this.escapeHtml(schema.description)}</span>`
      : "";

    // Build options from variants
    let options = "";
    for (const variant of variants) {
      const resolved = this.resolveRef(variant, rootSchema);
      const typeValue =
        resolved.properties?.[discriminator]?.const ||
        resolved.properties?.[discriminator]?.default ||
        resolved.title ||
        "unknown";
      options += `<option value="${this.escapeHtml(typeValue)}">${this.escapeHtml(typeValue)}</option>`;
    }

    const variantTemplates = variants
      .map((variant, i) => {
        const resolved = this.resolveRef(variant, rootSchema);
        const typeValue =
          resolved.properties?.[discriminator]?.const ||
          resolved.properties?.[discriminator]?.default ||
          i;
        return `
              <template x-if="formData.${path}.${discriminator} === '${this.escapeHtml(String(typeValue))}'">
                <div>${this.renderObjectField(resolved, path, rootSchema, depth + 1)}</div>
              </template>
            `;
      })
      .join("");

    return `
      <div class="form-field form-field-union">
        <label>${this.escapeHtml(this.pathToLabel(path))} Type</label>
        ${desc}
        <select x-model="formData.${path}.${discriminator}" @change="onUnionTypeChange('${path}', $event.target.value)">
          ${options}
        </select>
        <div class="union-fields">
          ${variantTemplates}
        </div>
      </div>
    `;
  },

  pathToLabel(path) {
    if (!path) return "";
    const parts = path.split(".");
    const last = parts[parts.length - 1];
    // Convert snake_case/camelCase to Title Case
    return last
      .replace(/_/g, " ")
      .replace(/([A-Z])/g, " $1")
      .replace(/^./, (s) => s.toUpperCase())
      .trim();
  },

  /**
   * Escape HTML special characters to prevent XSS
   */
  escapeHtml(str) {
    if (typeof str !== "string") return str;
    const htmlEscapes = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return str.replace(/[&<>"']/g, (char) => htmlEscapes[char]);
  },

  /**
   * Generate full form HTML for a schema
   * Handles two formats:
   * 1. Standard JSON Schema: {properties: {field1: {...}, field2: {...}}}
   * 2. Parameter schema dict: {param1: {properties: {...}}, param2: {...}}
   */
  generateForm(schema, rootSchema = null) {
    rootSchema = rootSchema || schema;
    let html = "";

    if (schema.properties) {
      // Standard JSON Schema with properties at root
      for (const [key, propSchema] of Object.entries(schema.properties)) {
        html += this.renderField(propSchema, key, rootSchema, 0);
      }
    } else {
      // Parameter schema dict - each key is a parameter name with its own schema
      for (const [paramName, paramSchema] of Object.entries(schema)) {
        if (paramSchema && typeof paramSchema === "object") {
          // Use the parameter's schema as the rootSchema for $ref resolution
          const paramRoot = paramSchema.$defs ? paramSchema : rootSchema;
          html += this.renderField(paramSchema, paramName, paramRoot, 0);
        }
      }
    }

    return html;
  },
};

// Export for use in policy_config.js
window.FormRenderer = FormRenderer;
