// hf-frontend/src/main.jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './App.css'

// ✅ Dable 완전 차단 (최강 버전)
(function() {
    // 1. window 객체에서 Dable 제거
    delete window.Dable;
    delete window.__dable;
    delete window.dable;
    
    // 2. fetch 가로채기
    const originalFetch = window.fetch;
    window.fetch = function(...args) {
        const url = args[0];
        if (url && typeof url === 'string' && 
            (url.includes('dable') || url.includes('scupio'))) {
            console.log('🚫 Dable fetch 차단됨');
            return Promise.reject(new Error('Dable blocked'));
        }
        return originalFetch.apply(this, args);
    };
    
    // 3. XMLHttpRequest 가로채기
    const originalXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = function() {
        const xhr = new originalXHR();
        const originalOpen = xhr.open;
        xhr.open = function(method, url, ...rest) {
            if (url && typeof url === 'string' && 
                (url.includes('dable') || url.includes('scupio'))) {
                console.log('🚫 Dable XHR 차단됨');
                return;
            }
            return originalOpen.call(this, method, url, ...rest);
        };
        return xhr;
    };
    
    // 4. 모든 script 태그에서 Dable 제거
    document.addEventListener('DOMContentLoaded', function() {
        const scripts = document.querySelectorAll('script');
        scripts.forEach(script => {
            if (script.src && (script.src.includes('dable') || script.src.includes('scupio'))) {
                script.remove();
                console.log('🚫 Dable 스크립트 제거됨');
            }
        });
    });
    
    console.log('✅ Dable 차단 활성화됨');
})();

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);