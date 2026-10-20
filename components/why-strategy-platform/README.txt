WHY autonomous strategy pipeline

This is the implementation deployed at /opt/why-strategy-platform. The current
rules and operational controls are documented in docs/operations.txt.
docs/product-design.txt and policy.py are the earlier planning prototype; they
are not loaded by the deployed runtime and do not define promotion requirements.

Current executable modules: strategy_platform/, export_hypotheses.py.
Reviewed Yellow integration: integration/customer/{ai_gateway,service,tenants}.py
and integration/execution/{ai_policy,engine,geometry}.py. Other integration files
are a staging copy of the existing application for offline regression testing.

Runtime requirements: Python 3.11+ and NumPy. Uses the server's installed Python
and NumPy; no changes were made to dependencies of existing services.

Offline checks:
  python3 -m unittest discover -s tests -v
  cd integration
  python3 -m unittest discover -s tests/execution -t .

Do not run execution tests with exchange-arming environment variables set.
The existing test harness blocks network and subprocess calls.

Services start at boot. No deployment occurs by running this README's commands.
Operational status: /opt/why-strategy-platform/state/status.json
Deployment evidence and backups: review/deployment/
