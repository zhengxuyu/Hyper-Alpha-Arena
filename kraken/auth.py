import yaml


def get_auth():
    with open('.api.yaml', 'r') as file:
        api = yaml.safe_load(file)

        api_key = api['API']
        pvi = api['PVI']
        return api_key, pvi