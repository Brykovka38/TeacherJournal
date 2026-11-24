if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/static/sw.js', {scope: '/'}).catch(error => console.warn('PWA:', error));
}
