/**
 * Razorpay checkout integration.
 * Creates an order via backend, opens Razorpay modal, verifies payment.
 */

import { apiUrl } from './api'
import { getToken, fetchUser } from './auth'

declare global {
  interface Window {
    Razorpay: any
  }
}

interface SubscribeOptions {
  plan: 'pro' | 'plus' | 'creator'
  cycle: 'monthly' | 'annual'
  onSuccess: (data: { plan: string; credits: number }) => void
  onError: (message: string) => void
}

interface TopupOptions {
  pack: 'starter' | 'popular' | 'power'
  onSuccess: (data: { credits_added: number; total_credits: number }) => void
  onError: (message: string) => void
}

async function authHeaders() {
  const token = getToken()
  if (!token) throw new Error('Not logged in')
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  }
}

/**
 * Subscribe to a plan — creates order, opens Razorpay, verifies payment.
 */
export async function subscribeToPlan({ plan, cycle, onSuccess, onError }: SubscribeOptions) {
  try {
    const headers = await authHeaders()

    // 1. Create order on backend
    const orderRes = await fetch(apiUrl('/billing/subscribe'), {
      method: 'POST',
      headers,
      body: JSON.stringify({ plan, cycle }),
    })
    if (!orderRes.ok) {
      const err = await orderRes.json()
      throw new Error(err.detail || 'Failed to create order')
    }
    const order = await orderRes.json()

    // 2. Open Razorpay checkout
    const options = {
      key: order.key_id,
      amount: order.amount,
      currency: order.currency,
      name: 'Prysm',
      description: `${plan.charAt(0).toUpperCase() + plan.slice(1)} Plan — ${cycle}`,
      order_id: order.order_id,
      prefill: {
        email: order.user_email,
        name: order.user_name,
      },
      theme: {
        color: '#080808',
        backdrop_color: 'rgba(0,0,0,0.6)',
      },
      modal: {
        confirm_close: true,
        escape: true,
      },
      handler: async (response: { razorpay_payment_id: string; razorpay_order_id: string; razorpay_signature: string }) => {
        try {
          // 3. Verify payment on backend
          const verifyRes = await fetch(apiUrl('/billing/verify'), {
            method: 'POST',
            headers,
            body: JSON.stringify({
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
              plan,
              cycle,
            }),
          })
          if (!verifyRes.ok) {
            const err = await verifyRes.json()
            throw new Error(err.detail || 'Payment verification failed')
          }
          const result = await verifyRes.json()

          // 4. Refresh user data in localStorage
          await fetchUser()

          onSuccess({ plan: result.plan, credits: result.credits })
        } catch (err: any) {
          onError(err.message || 'Payment verification failed')
        }
      },
    }

    const rzp = new window.Razorpay(options)
    rzp.on('payment.failed', (response: any) => {
      onError(response.error?.description || 'Payment failed')
    })
    rzp.open()
  } catch (err: any) {
    onError(err.message || 'Something went wrong')
  }
}

/**
 * Buy a credit top-up pack.
 */
export async function buyTopup({ pack, onSuccess, onError }: TopupOptions) {
  try {
    const headers = await authHeaders()

    const orderRes = await fetch(apiUrl('/billing/topup'), {
      method: 'POST',
      headers,
      body: JSON.stringify({ pack }),
    })
    if (!orderRes.ok) {
      const err = await orderRes.json()
      throw new Error(err.detail || 'Failed to create top-up order')
    }
    const order = await orderRes.json()

    const options = {
      key: order.key_id,
      amount: order.amount,
      currency: order.currency,
      name: 'Prysm',
      description: `${order.credits} Credit Top-up`,
      order_id: order.order_id,
      theme: {
        color: '#080808',
        backdrop_color: 'rgba(0,0,0,0.6)',
      },
      handler: async (response: { razorpay_payment_id: string; razorpay_order_id: string; razorpay_signature: string }) => {
        try {
          const verifyRes = await fetch(apiUrl('/billing/topup/verify'), {
            method: 'POST',
            headers,
            body: JSON.stringify({
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
              pack,
            }),
          })
          if (!verifyRes.ok) {
            const err = await verifyRes.json()
            throw new Error(err.detail || 'Top-up verification failed')
          }
          const result = await verifyRes.json()
          await fetchUser()
          onSuccess({ credits_added: result.credits_added, total_credits: result.total_credits })
        } catch (err: any) {
          onError(err.message || 'Top-up verification failed')
        }
      },
    }

    const rzp = new window.Razorpay(options)
    rzp.on('payment.failed', (response: any) => {
      onError(response.error?.description || 'Payment failed')
    })
    rzp.open()
  } catch (err: any) {
    onError(err.message || 'Something went wrong')
  }
}
