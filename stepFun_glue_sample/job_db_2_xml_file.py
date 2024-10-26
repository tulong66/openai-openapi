import yaml
import boto3
import logging
import pymysql
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Any
import os
import io

# ロガーの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DB2XMLConverter:
    def __init__(self, config_path: str):
        """
        コンバーターの初期化
        :param config_path: 設定ファイルのパス
        """
        self.config = self._load_config(config_path)
        self.connection = None
    
    def _load_config(self, config_path: str) -> Dict:
        """
        YAMLファイルから設定を読み込む
        """
        s3 = boto3.client('s3')
        try:
            bucket, key = config_path.split('/', 1)
            obj = s3.get_object(Bucket=bucket, Key=key)
            config_content = obj['Body'].read().decode('utf-8')
            return yaml.safe_load(io.StringIO(config_content))
        except Exception as e:
            logger.error(f"設定ファイルの読み込みに失敗しました: {str(e)}")
            raise

    def _get_db_credentials(self) -> Dict:
        """
        Secrets Managerからデータベース認証情報を取得
        """
        try:
            secret_arn = self.config['database']['secret_arn']
            session = boto3.session.Session()
            client = session.client('secretsmanager')
            response = client.get_secret_value(SecretId=secret_arn)
            return yaml.safe_load(response['SecretString'])
        except Exception as e:
            logger.error(f"認証情報の取得に失敗しました: {str(e)}")
            raise

    def _connect_db(self):
        """
        データベースに接続
        """
        try:
            credentials = self._get_db_credentials()
            self.connection = pymysql.connect(
                host=credentials['host'],
                user=credentials['username'],
                password=credentials['password'],
                db=credentials['dbname'],
                charset='utf8mb4'
            )
            logger.info("データベースに接続しました")
        except Exception as e:
            logger.error(f"データベース接続に失敗しました: {str(e)}")
            raise

    def _transform_value(self, value: Any, mapping: Dict) -> str:
        """
        設定に基づいて値を変換
        """
        if value is None:
            if mapping.get('nullable', False):
                return ""
            for transform in self.config['etl'].get('transformations', []):
                if transform['column'] == mapping['source_column'] and transform['type'] == 'replace_null':
                    return transform['value']
            return ""

        # データ型に基づく変換
        if mapping['type'] == 'datetime' and 'format' in mapping:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").strftime(mapping['format'])
        elif mapping['type'] == 'integer':
            return str(int(value))
        elif mapping['type'] == 'string':
            value = str(value)
            # 文字列変換ルールの適用
            for transform in self.config['etl'].get('transformations', []):
                if transform['column'] == mapping['source_column']:
                    if transform['type'] == 'upper_case':
                        value = value.upper()
            return value
        
        return str(value)

    def _create_xml(self, data: List[Dict]) -> ET.Element:
        """
        データをXML形式に変換
        """
        root = ET.Element(self.config['output']['root_element'])
        
        for row in data:
            record = ET.SubElement(root, self.config['output']['record_element'])
            for mapping in self.config['etl']['mappings']:
                value = row.get(mapping['source_column'])
                transformed_value = self._transform_value(value, mapping)
                element = ET.SubElement(record, mapping['target_element'])
                element.text = transformed_value

        return root

    def execute(self):
        """
        ETLプロセスを実行
        """
        logger.info("ETLプロセスを開始します")
        try:
            # データベース接続
            logger.info("データベースへの接続を開始します")
            self._connect_db()
            logger.info("データベースへの接続に成功しました")
            
            # クエリ実行
            logger.info("クエリの実行を開始します")
            with self.connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(self.config['etl']['source_query'])
                data = cursor.fetchall()
                logger.info(f"{len(data)}件のレコードを取得しました")

            # XML生成
            logger.info("XMLの生成を開始します")
            root = self._create_xml(data)
            tree = ET.ElementTree(root)
            logger.info("XMLの生成に成功しました")
            
            # XML出力
            logger.info("XMLファイルの出力を開始します")
            output_path = self.config['output']['file_path']
            output_dir = os.path.dirname(output_path)
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            tree.write(
                output_path,
                encoding=self.config['output']['encoding'],
                xml_declaration=True
            )
            logger.info(f"XMLファイルを出力しました: {output_path}")

        except FileNotFoundError as e:
            logger.error(f"設定ファイルが見つかりません: {str(e)}")
            raise
        except pymysql.err.OperationalError as e:
            logger.error(f"データベース接続に失敗しました: {str(e)}")
            raise
        except Exception as e:
            logger.exception(f"処理中にエラーが発生しました: {str(e)}")
            raise
        
        finally:
            if self.connection:
                logger.info("データベース接続を閉じます")
                self.connection.close()
                logger.info("データベース接続を閉じました")
            logger.info("ETLプロセスを終了します")

if __name__ == "__main__":
    try:
        converter = DB2XMLConverter(f"{os.environ['S3_BUCKET_LOCATION']}/stepFun_glue_sample/config/job_db2xml_config.yaml")
        converter.execute()
    except Exception as e:
        logger.error(f"プログラムが異常終了しました: {str(e)}")
        exit(1)
