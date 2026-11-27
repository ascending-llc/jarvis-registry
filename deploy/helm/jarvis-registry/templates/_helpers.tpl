{{/*
Expand the name of the chart.
*/}}
{{- define "jarvis-registry.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "jarvis-registry.fullname" -}}
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
Create chart name and version as used by the chart label.
*/}}
{{- define "jarvis-registry.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "jarvis-registry.labels" -}}
helm.sh/chart: {{ include "jarvis-registry.chart" . }}
{{ include "jarvis-registry.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "jarvis-registry.selectorLabels" -}}
app.kubernetes.io/name: {{ include "jarvis-registry.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Image tag for the registry's own images. Falls back to .Chart.AppVersion
when registry.imageTag is unset, so a chart bump moves all three images in
lockstep without a values override.
*/}}
{{- define "jarvis-registry.imageTag" -}}
{{- .Values.registry.imageTag | default .Chart.AppVersion -}}
{{- end }}

{{/*
Service account name for registry workloads.
Defaults to <fullname>-sa; override with registry.serviceAccount.name.
*/}}
{{- define "jarvis-registry.serviceAccountName" -}}
{{- .Values.registry.serviceAccount.name | default (printf "%s-sa" (include "jarvis-registry.fullname" .)) }}
{{- end }}

{{/* SecretStore name; defaults to <fullname>-secretstore. */}}
{{- define "jarvis-registry.secretStoreName" -}}
{{- .Values.registry.env.secretStoreRef.name | default (printf "%s-secretstore" (include "jarvis-registry.fullname" .)) -}}
{{- end }}

{{/* Registry-specific reloader annotation so it doesn't touch Chat's pods. */}}
{{- define "jarvis-registry.reloaderAnnotation" -}}
jarvis-registry.reloader/auto
{{- end }}

{{/* Override DB env vars from envFrom when a built-in database is enabled. */}}
{{- define "jarvis-registry.databaseEnv" -}}
{{- $fullname := include "jarvis-registry.fullname" . -}}
{{- if .Values.registry.mongodb.enabled }}
# $(MONGODB_ADMIN_PASSWORD) expands from envFrom (verified end-to-end in AS-2036).
# Clear username/password vars so stale keys in the Secret don't override MONGO_URI.
- name: MONGO_URI
  value: {{ printf "mongodb://%s:$(MONGODB_ADMIN_PASSWORD)@%s-mongodb-0.%s-mongodb:27017/%s?replicaSet=rs0&authSource=admin" .Values.registry.mongodb.auth.username $fullname $fullname .Values.registry.mongodb.database | quote }}
- name: MONGODB_USERNAME
  value: ""
- name: MONGODB_PASSWORD
  value: ""
{{- end }}
{{- if .Values.registry.redis.enabled }}
- name: REDIS_URI
  value: {{ printf "redis://%s-redis:6379" $fullname | quote }}
{{- end }}
{{- if .Values.registry.weaviate.enabled }}
- name: WEAVIATE_HOST
  value: {{ printf "%s-weaviate" $fullname | quote }}
- name: WEAVIATE_PORT
  value: "8080"
- name: WEAVIATE_GRPC_PORT
  value: "50051"
{{- end }}
{{- end }}

{{/* Extra env vars (map -> env entries), rendered after databaseEnv. */}}
{{- define "jarvis-registry.extraEnv" -}}
{{- $extra := .Values.registry.extraEnv -}}
{{- range $key := keys $extra | sortAlpha }}
- name: {{ $key }}
  value: {{ printf "%v" (index $extra $key) | quote }}
{{- end }}
{{- end }}
