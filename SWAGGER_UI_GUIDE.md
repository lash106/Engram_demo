# Swagger UI Setup Guide

This guide explains how to set up and use Swagger UI to interactively test the Engram API.

## Option 1: Online Swagger Editor (Recommended for Quick Testing)

The easiest way to test the API is using the online Swagger Editor:

1. **Go to**: https://editor.swagger.io/

2. **Load the Engram API**:
   - Click **File** → **Load URL**
   - Paste: `https://raw.githubusercontent.com/lash106/Engram/main/openapi.yaml`
   - Or paste your local file URL if hosting locally

3. **Start making requests**:
   - Select an endpoint from the left sidebar
   - Click **Try it out**
   - Fill in parameters
   - Click **Execute**
   - See the response in real-time

**Advantages**:

- No installation required
- Works in browser
- Beautiful UI
- Immediate feedback

**Disadvantages**:

- Requires internet connection
- Can't modify spec easily
- Limited to public APIs or local dev with CORS

---

## Option 2: Local Swagger UI with Docker

Run Swagger UI locally in Docker:

```bash
# Start Swagger UI pointing to your OpenAPI spec
docker run -p 8080:8080 -e SWAGGER_JSON=/openapi.yaml \
  -v $(pwd)/openapi.yaml:/openapi.yaml \
  swaggerapi/swagger-ui

# Access at: http://localhost:8080
```

**Advantages**:

- Full control
- Works offline
- Fast local loading

**Disadvantages**:

- Requires Docker
- Need to restart container when spec changes

---

## Option 3: Swagger UI with Python (Using FastAPI)

If you're running Engram with FastAPI, Swagger UI is automatically included!

### Enable Swagger in Your FastAPI App

The Engram API already includes built-in Swagger UI. Access it at:

```
http://localhost:8000/docs
```

**What you get automatically**:

- ✅ Full interactive API documentation
- ✅ Auto-generated from OpenAPI spec
- ✅ Try-it-out functionality
- ✅ Real-time request/response
- ✅ Schema validation
- ✅ Authentication helpers (when needed)

### Alternative FastAPI Documentation

FastAPI provides two documentation endpoints:

| Endpoint | UI Framework | Features                           |
| -------- | ------------ | ---------------------------------- |
| `/docs`  | Swagger UI   | Interactive testing, detailed      |
| `/redoc` | ReDoc        | Beautiful, read-only documentation |

Visit both:

- Swagger: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## Option 4: Manual Swagger UI Setup (Advanced)

If you need custom Swagger UI setup:

### 1. Install Swagger UI package

```bash
npm install -g swagger-ui-dist
# or
pip install swagger-ui-py
```

### 2. Serve OpenAPI spec as static file

```bash
# Copy openapi.yaml to a web-accessible location
cp openapi.yaml /var/www/html/
```

### 3. Create custom Swagger UI HTML

```html
<!DOCTYPE html>
<html>
  <head>
    <title>Engram API Docs</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link
      rel="stylesheet"
      href="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/4.15.5/swagger-ui.min.css"
    />
  </head>
  <body>
    <div id="swagger-ui"></div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/4.15.5/swagger-ui.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/4.15.5/swagger-ui-bundle.min.js"></script>
    <script>
      window.onload = function () {
        SwaggerUIBundle({
          url: "/openapi.yaml", // Path to your OpenAPI spec
          dom_id: "#swagger-ui",
          presets: [
            SwaggerUIBundle.presets.apis,
            SwaggerUIBundle.SwaggerUIStandalonePreset,
          ],
          layout: "StandaloneLayout",
        });
      };
    </script>
  </body>
</html>
```

---

## Using Swagger UI to Test Engram

### Step 1: Start the Engram API

```bash
docker-compose up -d
# or
python -m uvicorn engram.api:app --host 0.0.0.0 --port 8000
```

### Step 2: Access Swagger UI

**If using FastAPI's built-in Swagger**:

```
http://localhost:8000/docs
```

**If using standalone Swagger**:

```
http://localhost:8080
```

### Step 3: Test Register a Role

1. Find **POST /roles** in the left sidebar
2. Click **Try it out**
3. Fill in the request body:
   ```json
   {
     "role_name": "admin",
     "can_read": ["*"],
     "can_write": ["*"]
   }
   ```
4. Click **Execute**
5. See the response: `200 OK` with role details

### Step 4: Test Write Operation

1. Find **POST /write**
2. Click **Try it out**
3. Fill in the request body:
   ```json
   {
     "key": "budget",
     "value": 5000,
     "agent_id": "agent_a",
     "role": "admin",
     "vector_clock": {
       "agent_a": 1
     }
   }
   ```
4. Click **Execute**
5. See the MemoryEntry response with `write_id`, `status: "ok"`, etc.

### Step 5: Test Read Operation

1. Find **GET /read/{key}**
2. Click **Try it out**
3. Fill in:
   - **key**: `budget`
   - **agent_id**: `agent_a` (query param)
   - **role**: `admin` (query param)
4. Click **Execute**
5. See the current value

---

## Swagger UI Features

### Request Body Validation

Swagger UI validates your request before sending:

- **Green checkmark** = Valid JSON
- **Red X** = Invalid (missing required fields, wrong types)
- Errors shown inline with helpful messages

### Response Visualization

Swagger shows responses in multiple formats:

- **200** responses: Formatted JSON with schema
- **400** errors: Error message with details
- **403** errors: Permission denied explanation
- **404** errors: Resource not found message

### Schema Exploration

Click on any schema to expand and see:

- Field names and types
- Required vs optional fields
- Field descriptions
- Example values
- Nested objects

### Header Parameters

For endpoints with headers:

1. Scroll down in **Try it out** section
2. Find **Headers** section
3. Add custom headers if needed

### Authentication

If your API requires authentication:

1. Look for **lock icon** next to endpoint
2. Click it to enter credentials
3. Headers are automatically added to requests

---

## Troubleshooting

### "Failed to fetch" Error

**Cause**: CORS issue or invalid URL

**Fix**:

- Verify API is running: `curl http://localhost:8000/health`
- Check API URL is correct
- Enable CORS in FastAPI:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### "Cannot find endpoint"

**Cause**: Spec file doesn't match running API

**Fix**:

- Ensure openapi.yaml is up-to-date
- Restart Swagger UI to reload spec
- Clear browser cache (Ctrl+Shift+Delete)

### Request Hangs

**Cause**: API is slow or unresponsive

**Fix**:

- Check API logs for errors
- Verify storage adapter is healthy: `GET /health`
- Check network connection

### Can't See Response Schema

**Cause**: Schema definition missing in OpenAPI spec

**Fix**:

- Verify the endpoint response includes `$ref: #/components/schemas/...`
- Check schema exists in `components.schemas`
- Refresh Swagger UI

---

## Integration with CI/CD

### Generate API Documentation

Use Swagger CodeGen to generate client libraries:

```bash
# Generate Python client
docker run --rm -v $(pwd):/local swaggerapi/swagger-codegen-cli generate \
  -i /local/openapi.yaml \
  -l python \
  -o /local/clients/python

# Generate JavaScript client
docker run --rm -v $(pwd):/local swaggerapi/swagger-codegen-cli generate \
  -i /local/openapi.yaml \
  -l javascript \
  -o /local/clients/javascript
```

### Publish to SwaggerHub

```bash
# Create account at https://app.swaggerhub.com/
# Then publish your spec:

curl -X POST "https://api.swaggerhub.com/apis/YOUR_ORG/engram/0.1.0" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/yaml" \
  --data-binary @openapi.yaml
```

### Automated Documentation

Include Swagger UI in your CI/CD:

```yaml
# .github/workflows/docs.yml
name: Generate API Docs

on: [push]

jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2

      - name: Validate OpenAPI spec
        run: |
          npm install -g swagger-cli
          swagger-cli validate openapi.yaml

      - name: Generate HTML docs
        run: |
          npm install -g redoc-cli
          redoc-cli build openapi.yaml -o docs/index.html

      - name: Deploy docs
        uses: peaceiris/actions-gh-pages@v3
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./docs
```

---

## Best Practices

1. **Keep openapi.yaml in version control**: Track changes to API specification
2. **Validate spec regularly**: Use `swagger-cli validate` or similar
3. **Keep descriptions updated**: Help users understand endpoints
4. **Include examples**: Make it easier for developers to understand
5. **Document error codes**: Help troubleshooting
6. **Version your API**: Use major.minor.patch in OpenAPI info.version
7. **Generate clients automatically**: Use swagger-codegen to keep clients in sync

---

## Additional Resources

- **Swagger UI Official**: https://swagger.io/tools/swagger-ui/
- **OpenAPI Specification**: https://spec.openapis.org/oas/v3.0.3
- **Swagger Editor**: https://editor.swagger.io/
- **ReDoc**: https://redoc.ly/
- **SwaggerHub**: https://swaggerhub.com/

---

## Quick Reference: FastAPI Built-in Swagger

Engram uses **FastAPI**, which automatically provides Swagger UI at `/docs`.

**Key URLs**:

- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Health check: `http://localhost:8000/health`

**FastAPI's Swagger Features**:

- ✅ Auto-generated from code
- ✅ Real-time API testing
- ✅ Schema validation
- ✅ Request/response examples
- ✅ Authorization helpers
- ✅ Works offline
- ✅ No additional setup needed

---

## Starting Your First Test

1. **Ensure API is running**:

   ```bash
   docker-compose up -d
   ```

2. **Open browser**:

   ```
   http://localhost:8000/docs
   ```

3. **Follow the Basic Workflow** (see API_USAGE_GUIDE.md):
   - Register admin role
   - Write a value
   - Read the value
   - Check history

4. **Explore other endpoints** at your own pace

Happy testing! 🚀
