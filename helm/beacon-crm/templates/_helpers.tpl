{{/*
Expand the name of the chart.
*/}}
{{- define "beacon.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fullname: release-name + chart-name, truncated to 63 chars.
*/}}
{{- define "beacon.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "beacon.labels" -}}
helm.sh/chart: {{ include "beacon.name" . }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{/*
Selector labels for a given component.
Usage: {{ include "beacon.selectorLabels" (dict "context" . "component" "backend") }}
*/}}
{{- define "beacon.selectorLabels" -}}
app.kubernetes.io/name: {{ include "beacon.name" .context }}
app.kubernetes.io/instance: {{ .context.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Image helper — respects global registry override.
Usage: {{ include "beacon.image" (dict "image" .Values.backend.image "global" .Values.global) }}
*/}}
{{- define "beacon.image" -}}
{{- if .global.imageRegistry -}}
{{ .global.imageRegistry }}/{{ .image.repository }}:{{ .image.tag }}
{{- else -}}
{{ .image.repository }}:{{ .image.tag }}
{{- end -}}
{{- end }}

{{/*
Database URL (async).
*/}}
{{- define "beacon.databaseUrl" -}}
postgresql+asyncpg://$(DB_USER):$(DB_PASSWORD)@{{ include "beacon.fullname" . }}-postgres:{{ .Values.postgres.port }}/$(DB_NAME)
{{- end }}

{{/*
Database URL (sync — for Alembic).
*/}}
{{- define "beacon.syncDatabaseUrl" -}}
postgresql://$(DB_USER):$(DB_PASSWORD)@{{ include "beacon.fullname" . }}-postgres:{{ .Values.postgres.port }}/$(DB_NAME)
{{- end }}

{{/*
Redis URL.
*/}}
{{- define "beacon.redisUrl" -}}
redis://{{ include "beacon.fullname" . }}-redis:{{ .Values.redis.port }}/0
{{- end }}

{{/*
Pod-level securityContext (applies to every container in the pod).
Sets the non-root uid/gid and the fsGroup so mounted volumes (e.g. the /tmp
emptyDir) are group-writable by the runtime user. uid/gid 10001 must match the
appuser baked into the backend Dockerfile.
Usage: {{- include "beacon.podSecurityContext" . | nindent 6 }}
*/}}
{{- define "beacon.podSecurityContext" -}}
securityContext:
  runAsNonRoot: {{ .Values.securityContext.runAsNonRoot }}
  runAsUser: {{ .Values.securityContext.runAsUser }}
  runAsGroup: {{ .Values.securityContext.runAsGroup }}
  fsGroup: {{ .Values.securityContext.fsGroup }}
{{- end }}

{{/*
Container-level securityContext.
Drops all Linux capabilities and blocks privilege escalation. readOnlyRootFilesystem
is opt-in per workload because some containers write scratch files to /tmp:
pass a dict with "readOnlyRootFilesystem" to override the global default.
Usage:
  {{- include "beacon.containerSecurityContext" (dict "context" . "readOnlyRootFilesystem" true) | nindent 10 }}
*/}}
{{- define "beacon.containerSecurityContext" -}}
{{- $readOnly := .context.Values.securityContext.readOnlyRootFilesystem -}}
{{- if hasKey . "readOnlyRootFilesystem" }}{{- $readOnly = .readOnlyRootFilesystem -}}{{- end }}
securityContext:
  allowPrivilegeEscalation: {{ .context.Values.securityContext.allowPrivilegeEscalation }}
  readOnlyRootFilesystem: {{ $readOnly }}
  capabilities:
    drop:
      - ALL
{{- end }}
