import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { getCustomer, getCustomers, getSegments, getSummary } from '@/api/admin'

const RESOURCE_KEYS = ['customers', 'customerDetail', 'summary', 'segments']

function emptyCustomerMeta() {
  return { total: 0, page: 1, page_size: 20, total_pages: 1, generated_at: null }
}

function emptyResourceState(factory) {
  return Object.fromEntries(RESOURCE_KEYS.map(key => [key, factory()]))
}

function paramsKey(prefix, params) {
  const entries = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .sort(([left], [right]) => left.localeCompare(right))
  return `${prefix}:${JSON.stringify(Object.fromEntries(entries))}`
}

export const useAdminStore = defineStore('admin', () => {
  const customers = ref([])
  const customerListMeta = ref(emptyCustomerMeta())
  const customerDetail = ref(null)
  const activeCustomerId = ref(null)
  const summary = ref(null)
  const summaryMeta = ref({ generated_at: null })
  const segments = ref(null)
  const segmentsMeta = ref({ generated_at: null })

  const loadingRequestKeys = ref(emptyResourceState(() => null))
  const requestErrors = ref(emptyResourceState(() => null))
  const activeRequestKeys = emptyResourceState(() => null)
  const inFlight = new Map()
  let cacheGeneration = 0

  const customerListLoading = computed(() => loadingRequestKeys.value.customers !== null)
  const customerDetailLoading = computed(() => loadingRequestKeys.value.customerDetail !== null)
  const summaryLoading = computed(() => loadingRequestKeys.value.summary !== null)
  const segmentsLoading = computed(() => loadingRequestKeys.value.segments !== null)

  const customerListError = computed(() => requestErrors.value.customers)
  const customerDetailError = computed(() => requestErrors.value.customerDetail)
  const summaryError = computed(() => requestErrors.value.summary)
  const segmentsError = computed(() => requestErrors.value.segments)

  function setError(resource, error) {
    requestErrors.value = { ...requestErrors.value, [resource]: error }
  }

  function setLoading(resource, key) {
    loadingRequestKeys.value = { ...loadingRequestKeys.value, [resource]: key }
  }

  function runRequest(resource, key, requestFn, applyResponse) {
    activeRequestKeys[resource] = key
    setError(resource, null)
    setLoading(resource, key)

    const pending = inFlight.get(key)
    if (pending) return pending

    const generation = cacheGeneration

    const request = Promise.resolve()
      .then(requestFn)
      .then(body => {
        if (generation === cacheGeneration && activeRequestKeys[resource] === key) {
          applyResponse(body)
        }
        return body
      })
      .catch(error => {
        if (generation === cacheGeneration && activeRequestKeys[resource] === key) {
          setError(resource, error)
        }
        return null
      })
      .finally(() => {
        if (generation === cacheGeneration && activeRequestKeys[resource] === key) {
          setLoading(resource, null)
        }
        if (inFlight.get(key) === request) inFlight.delete(key)
      })

    inFlight.set(key, request)
    return request
  }

  function loadCustomers(params = {}) {
    const key = paramsKey('customers', params)
    return runRequest('customers', key, () => getCustomers(params), body => {
      customers.value = body.data ?? []
      customerListMeta.value = { ...emptyCustomerMeta(), ...body.meta }
    })
  }

  function loadCustomer(userId) {
    const normalizedId = String(userId)
    if (activeCustomerId.value !== normalizedId) customerDetail.value = null
    activeCustomerId.value = normalizedId

    const key = `customer-detail:${normalizedId}`
    return runRequest('customerDetail', key, () => getCustomer(userId), body => {
      customerDetail.value = body.data ?? null
    })
  }

  function loadSummary() {
    return runRequest('summary', 'summary', getSummary, body => {
      summary.value = body.data ?? null
      summaryMeta.value = { generated_at: null, ...body.meta }
    })
  }

  function loadSegments() {
    return runRequest('segments', 'segments', getSegments, body => {
      segments.value = body.data ?? null
      segmentsMeta.value = { generated_at: null, ...body.meta }
    })
  }

  function loadOverview() {
    return Promise.all([loadSummary(), loadSegments()])
  }

  function clearAdmin() {
    cacheGeneration += 1
    customers.value = []
    customerListMeta.value = emptyCustomerMeta()
    customerDetail.value = null
    activeCustomerId.value = null
    summary.value = null
    summaryMeta.value = { generated_at: null }
    segments.value = null
    segmentsMeta.value = { generated_at: null }
    loadingRequestKeys.value = emptyResourceState(() => null)
    requestErrors.value = emptyResourceState(() => null)
    for (const resource of RESOURCE_KEYS) activeRequestKeys[resource] = null
    inFlight.clear()
  }

  return {
    customers,
    customerListMeta,
    customerDetail,
    activeCustomerId,
    summary,
    summaryMeta,
    segments,
    segmentsMeta,
    customerListLoading,
    customerDetailLoading,
    summaryLoading,
    segmentsLoading,
    customerListError,
    customerDetailError,
    summaryError,
    segmentsError,
    loadCustomers,
    loadCustomer,
    loadSummary,
    loadSegments,
    loadOverview,
    clearAdmin,
  }
})
