import streamlit.components.v1 as components
import json
import streamlit as st


class BrowserLocalStorage:
    """Simple browser local storage implementation using JavaScript"""

    def __init__(self):
        self.initialized = False

    def setItem(self, key, value):
        """Set item in browser local storage using JavaScript"""
        try:
            # Escape quotes in the value
            if isinstance(value, str):
                value = value.replace("'", "\\'").replace('"', '\\"')

            components.html(
                f"""
                <script>
                localStorage.setItem('{key}', `{value}`);
                console.log('LocalStorage set: {key} =', `{value}`);
                </script>
                """,
                height=0,
                width=0,
            )
            return True
        except Exception as e:
            print(f"Error setting local storage item: {e}")
            return False

    def getItem(self, key):
        """Get item from browser local storage using JavaScript"""
        try:
            # Create a custom component to retrieve the value
            result = components.html(
                f"""
                <script>
                const value = localStorage.getItem('{key}');
                if (value) {{
                    document.write('<div id="{key}_value" style="display:none;">' + value + '</div>');
                }}
                </script>
                <div id="{key}_container"></div>
                """,
                height=0,
                width=0,
            )

            # This approach has limitations - let's try a different method
            return self._getItemViaSession(key)

        except Exception as e:
            print(f"Error getting local storage item: {e}")
            return None

    def _getItemViaSession(self, key):
        """Alternative method using session state to store retrieved values"""
        try:
            # Use a unique session key
            session_key = f"local_storage_{key}"

            if session_key not in st.session_state:
                # Initialize with JavaScript to get the value
                components.html(
                    f"""
                    <script>
                    const value = localStorage.getItem('{key}');
                    if (value) {{
                        // Send the value back to Streamlit via URL parameters
                        window.location.href = window.location.href + '?{key}=' + encodeURIComponent(value);
                    }}
                    </script>
                    """,
                    height=0,
                    width=0,
                )
                return None

            return {"value": st.session_state[session_key]}

        except Exception as e:
            print(f"Error in session-based getItem: {e}")
            return None

    def getItemSimple(self, key):
        """Simplified get method that might work better"""
        try:
            # Use a more direct approach
            js_code = f"""
            <script>
            var value = localStorage.getItem('{key}');
            if (value) {{
                document.write(value);
            }}
            </script>
            """

            # This will display the value, but we need to capture it
            # For now, let's use a different approach
            return self._getViaQueryParams(key)

        except Exception as e:
            print(f"Error in simple getItem: {e}")
            return None

    def _getViaQueryParams(self, key):
        """Try to get values via query parameters"""
        try:
            query_params = st.query_params
            if key in query_params:
                value = query_params[key]
                return {"value": value}
            return None
        except:
            return None
