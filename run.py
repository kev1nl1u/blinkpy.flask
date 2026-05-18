import os
from dotenv import load_dotenv

load_dotenv()

from app import create_app

app = create_app()

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() in ('1', 'true', 'yes')

    if debug:
        from livereload import Server
        app.debug = True
        server = Server(app.wsgi_app)
        server.watch('app/templates/*')
        server.watch('app/static/*')
        print('Dev server with LiveReload: http://127.0.0.1:5000')
        server.serve(port=5000, host='127.0.0.1')
    else:
        print('Production server: http://0.0.0.0:5000')
        app.run(debug=False, port=5000, host='0.0.0.0', threaded=True)
