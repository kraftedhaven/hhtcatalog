// Function to force-save credentials without reloading the page
function saveSettings(event) {
    if (event) event.preventDefault(); // Prevents page refresh/reload!

    const geminiKey = document.getElementById('cfg-gemini').value.trim();
    const endpoint = document.getElementById('cfg-endpoint').value.trim() || 'https://nyc.cloud.appwrite.io/v1';
    const projectId = document.getElementById('cfg-project').value.trim();

    // Write directly to browser localStorage
    localStorage.setItem('cfg_gemini', geminiKey);
    localStorage.setItem('cfg_endpoint', endpoint);
    localStorage.setItem('cfg_project', projectId);

    // Update button display
    const statusBtn = document.getElementById('nav-key-status');
    if (statusBtn) {
        statusBtn.innerText = "Keys Locked & Saved";
        statusBtn.classList.add('text-emerald-400');
    }

    alert("Keys permanently saved to browser memory!");
    
    // Close modal
    const modal = document.getElementById('settings-modal');
    if (modal) modal.classList.add('hidden');
}

// Automatically re-load saved keys when the page opens
window.addEventListener('DOMContentLoaded', () => {
    if (localStorage.getItem('cfg_gemini')) {
        document.getElementById('cfg-gemini').value = localStorage.getItem('cfg_gemini');
    }
    if (localStorage.getItem('cfg_endpoint')) {
        document.getElementById('cfg-endpoint').value = localStorage.getItem('cfg_endpoint');
    }
    if (localStorage.getItem('cfg_project')) {
        document.getElementById('cfg-project').value = localStorage.getItem('cfg_project');
    }
});