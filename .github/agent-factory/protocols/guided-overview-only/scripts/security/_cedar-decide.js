'use strict'
// The ONE place that touches the cedar-wasm authorize API. Every caller goes through decide().
//
// Real API (cedar-wasm v4.11.2):
//   cedar.isAuthorized(AuthorizationCall) -> AuthorizationAnswer
//   No PolicySet.parse(), no Authorizer class — isAuthorized is a plain exported function.
//   principal/action/resource: EntityUidJson = { type, id } (not strings)
//   policies: PolicySet = { staticPolicies: <string|...> }
//   entities: Entities = Array<EntityJson> (not a JSON string)
//   decision: 'allow' | 'deny' (lowercase)
const cedar = require('@cedar-policy/cedar-wasm/nodejs')

/**
 * Parse a Cedar entity UID string like 'Type::"id"' into { type, id }.
 * Also passes through already-parsed objects ({ type, id } or { __entity: {...} }).
 * @param {string|object} uid
 * @returns {{ type: string, id: string } | object}
 */
function parseUid(uid) {
  if (typeof uid === 'string') {
    // Type group allows `::` so namespaced types parse (e.g. Aws::S3::Bucket::"x").
    // Greedy [\w:]+ backtracks to leave the final `::"id"` for the id capture.
    const m = uid.match(/^([\w:]+)::"([^"]*)"$/)
    if (!m) throw new Error('Invalid Cedar UID string: ' + uid)
    return { type: m[1], id: m[2] }
  }
  return uid
}

/**
 * decide(policiesText, entitiesJson, request) -> 'Allow' | 'Deny'
 *
 * @param {string} policiesText  Cedar policy set text (Cedar language)
 * @param {Array|string} entitiesJson  Entities array or JSON string
 * @param {{ principal: string|object, action: string|object, resource: string|object, context: object }} request
 * @returns {'Allow'|'Deny'}
 */
function decide(policiesText, entitiesJson, request) {
  const entities = typeof entitiesJson === 'string' ? JSON.parse(entitiesJson) : entitiesJson

  const call = {
    principal: parseUid(request.principal),
    action: parseUid(request.action),
    resource: parseUid(request.resource),
    context: request.context || {},
    policies: { staticPolicies: policiesText },
    entities,
  }

  const answer = cedar.isAuthorized(call)
  if (answer.type === 'failure') {
    const msgs = (answer.errors || []).map(e => e.message || String(e)).join('; ')
    throw new Error('Cedar authorization error: ' + msgs)
  }
  // answer.response.decision is 'allow' | 'deny'
  return answer.response.decision === 'allow' ? 'Allow' : 'Deny'
}

module.exports = { decide }
