# Current Objective

- Objective: Remove the PolicyEngine class and inline Redis client setup in the control plane app.
- Acceptance check: Control plane app boots without PolicyEngine, PolicyEngine module/tests are gone, and Redis client is constructed via the new helper function.

Update these fields the moment a new objective starts. Clear the file once the objective ships and the changelog has been updated.
