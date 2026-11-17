/* Public, non-secret build configuration.
 * DO NOT enter an operator invite code, JWT, S3 credential, or course file here.
 * Never enable a beta route until the operator API, CORS, student-response
 * projection, retention and deletion policies are independently verified.
 */
export const betaConfig=Object.freeze({
  sharedApiBaseUrl: '',
  apiDocumentationUrl: '',
  inviteSecretName: '', // GitHub Actions secret NAME only (never its value)
  retentionPolicy: '',
  deletionProcedure: '',
  coachingTimeoutSec: null,
  apiContractVerified: false,
  corsVerified: false,
  studentResponseProjectionVerified: false
});
