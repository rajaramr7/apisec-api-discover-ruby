module Api
  module V1
    class UsersController < ApplicationController
      before_action :authenticate_api_user!

      def index; end
      def show; end
      def create; end
      def update; end
      def destroy; end

      private

      def user_params
        params.require(:user).permit(:name, :email, :role)
      end
    end
  end
end
