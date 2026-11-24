// Local Jenkins for the BlackBox demo: one admin user, no anonymous access, one freestyle job.
// Runs at every start; it only creates what is missing. Core Jenkins only (no plugins needed).
import jenkins.model.Jenkins
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import hudson.model.FreeStyleProject
import hudson.model.ParametersDefinitionProperty
import hudson.model.StringParameterDefinition
import hudson.tasks.Shell
import jenkins.security.ApiTokenProperty

def j = Jenkins.get()
def user = System.getenv("JENKINS_ADMIN_USER") ?: "blackbox"
def pass = System.getenv("JENKINS_ADMIN_PASSWORD") ?: "change-me-locally"

if (!(j.securityRealm instanceof HudsonPrivateSecurityRealm)) {
  def realm = new HudsonPrivateSecurityRealm(false)
  realm.createAccount(user, pass)
  j.setSecurityRealm(realm)
  def auth = new FullControlOnceLoggedInAuthorizationStrategy()
  auth.setAllowAnonymousRead(false)
  j.setAuthorizationStrategy(auth)
  j.setNumExecutors(1)
  j.save()
}

// API token for read-only use by BlackBox, written once to a file only readable inside the container volume.
def tokenFile = new File(j.rootDir, "blackbox_api_token")
if (!tokenFile.exists()) {
  def u = hudson.model.User.getById(user, true)
  def prop = u.getProperty(ApiTokenProperty.class) ?: new ApiTokenProperty()
  u.addProperty(prop)
  def result = prop.tokenStore.generateNewToken("blackbox-readonly")
  u.save()
  tokenFile.text = result.plainValue
  tokenFile.setReadable(false, false)
  tokenFile.setReadable(true, true)
}

if (j.getItem("demo-shop-ci") == null) {
  def job = j.createProject(FreeStyleProject, "demo-shop-ci")
  job.setDescription("Builds the demo shop source at a commit SHA from GitHub (likhith2366/blackbox-demo-shop).")
  job.addProperty(new ParametersDefinitionProperty(
    new StringParameterDefinition("COMMIT", "b7ae74f40b4cbabbd9f731663eb42bf8b238b75f", "commit SHA to build")))
  job.buildersList.add(new Shell('''set -eu
echo "demo-shop-ci: building likhith2366/blackbox-demo-shop at ${COMMIT}"
curl -fsSL -o src.tgz "https://codeload.github.com/likhith2366/blackbox-demo-shop/tar.gz/${COMMIT}"
echo "source archive sha256: $(sha256sum src.tgz | cut -d' ' -f1)"
rm -rf src && mkdir src && tar -xzf src.tgz -C src --strip-components=1
echo "services:"; ls src/services
echo "payment-svc config:"; cat src/config/payment-svc.yaml
echo "http client policy:"; cat src/libs/httpclient/policy.py
test -f src/services/payment/app.py && test -f src/services/checkout/app.py
echo "demo-shop-ci: OK"
'''))
  job.save()
}
